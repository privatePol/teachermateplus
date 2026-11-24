from dataclasses import replace
from collections import Counter
from itertools import combinations
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, SimpleTestCase
from django.urls import reverse

from apps.rbac.models import UserPermission
from .generation_readiness import Stage6ReadinessService
from .generation_services import ExamGenerationService
from .models import (ExamGeneratorPolicy, GeneratedExamItem, GeneratedExamSet, Question,
                     _classification_service_scope, CycleCourse)
from .set_modes import (apply_choices, select_blocks, verify_choices, verify_policy_revision,
                        require_faculty_section_set)
from .stage4_test_support import Stage4TestCase
from .tests_stage6_generation import Stage6BGenerationFixtureMixin


class ChoicePermutationTests(SimpleTestCase):
    def item(self):
        from .generation_readiness import generation_question_source_digest
        values = dict(source_question_id=7, source_question_revision=1, choices_snapshot=["one", "two", "three", "four"],
                      correct_answer_snapshot="B", question_text_snapshot="Pick two", difficulty_snapshot="EASY",
                      question_content_format_snapshot="PLAIN_TEXT")
        values["source_question_digest"] = generation_question_source_digest(source_id=7, revision=1,
            question_text="Pick two", choices=values["choices_snapshot"], correct_answer="B", difficulty="EASY")
        return SimpleNamespace(**values)

    def test_choice_shuffle_remaps_key_and_retains_original(self):
        item = self.item()
        with patch("apps.departmental_exams.set_modes.confidential_hmac_rank", side_effect=[3, 2, 1, 0]):
            apply_choices(item, shuffle=True, fixed=False, seed="test")
        self.assertEqual(item.choices_snapshot, ["four", "three", "two", "one"])
        self.assertEqual(item.correct_answer_snapshot, "C")
        self.assertEqual(item.original_correct_answer_snapshot, "B")
        verify_choices(item, shuffle=True)
        item.correct_answer_snapshot = "B"
        with self.assertRaises(PermissionDenied):
            verify_choices(item, shuffle=True)

    def test_fixed_order_and_disabled_shuffle_preserve_choices(self):
        for shuffle, fixed in ((True, True), (False, False)):
            item = self.item()
            apply_choices(item, shuffle=shuffle, fixed=fixed, seed="test")
            self.assertEqual(item.choice_order_snapshot, [0, 1, 2, 3])
            self.assertEqual(item.correct_answer_snapshot, "B")
            verify_choices(item, shuffle=shuffle)

    def test_existing_label_dependent_items_are_fixed_without_new_flag(self):
        from .set_modes import choices_fixed_order
        question = SimpleNamespace(choices_fixed_order=False, content_format="PLAIN_TEXT",
            question_text="Choose the correct combination", choice_a="First fact", choice_b="Second fact",
            choice_c="Both A and B", choice_d="Neither A nor B")
        self.assertTrue(choices_fixed_order(question))

    def test_whole_unit_reuse_minimum_matches_small_exhaustive_oracle(self):
        from .generation_algorithms import IdentityBlock, IdentityMember
        specs = [("a", (1,), (1, 1, 0)), ("b", (2,), (1, 0, 1)),
                 ("copy-a", (3,), (1, 1, 0)), ("c", (4,), (1, 0, 1)),
                 ("case", (5, 6), (2, 1, 1))]
        fingerprints = {1: "a", 2: "b", 3: "a", 4: "c", 5: "a", 6: "d"}
        blocks = tuple(IdentityBlock(block_id=code, vector=vector,
            members=tuple(IdentityMember(q, q, "C", "EASY", 0) for q in ids)) for code, ids, vector in specs)
        problem = SimpleNamespace(blocks=blocks, margins=(2, 1, 1),
            questions={q: SimpleNamespace(normalized_fingerprint=fp) for q, fp in fingerprints.items()})
        for costs in ((0, 0, 0, 0), (2, 0, 1, 0), (0, 3, 2, 1)):
            usage = Counter(dict(zip("abcd", costs)))
            oracle = []
            for size in range(1, len(blocks) + 1):
                for subset in combinations(blocks, size):
                    ids = [fingerprints[m.source_id] for b in subset for m in b.members]
                    if (tuple(sum(b.vector[j] for b in subset) for j in range(3)) == problem.margins
                            and len(ids) == len(set(ids))):
                        oracle.append(sum(usage[i] for i in ids))
            chosen = select_blocks(problem, usage, seed="oracle", budget=1000)
            self.assertEqual(sum(usage[fingerprints[m.source_id]] for b in blocks if b.block_id in chosen
                                 for m in b.members), min(oracle))
        with self.assertRaisesMessage(ValidationError, "computation budget"):
            select_blocks(problem, Counter(), seed="limit", budget=0)


class SectionSetIntegrationTests(Stage6BGenerationFixtureMixin, Stage4TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from apps.rbac.models import Permission
        Permission.objects.get_or_create(code="faculty_portal.access", defaults={
            "module": "faculty_portal", "action": "access", "description": "Faculty access"})

    def policy_course(self, mode="DIFFERENT", shuffle=False):
        parent, _problem = self.ready_generation_course()
        with _classification_service_scope():
            CycleCourse.objects.filter(pk=parent.id).update(exam_classification="DEPARTMENTAL")
        parent.refresh_from_db()
        policy = ExamGeneratorPolicy.objects.create(cycle=parent.cycle, departmental=True,
            standardized=True, mode=mode, shuffle_choices=shuffle)
        return parent, policy

    def generate(self, parent):
        problem, report = Stage6ReadinessService.build_problem(cycle_course=parent)
        self.assertTrue(report["ready"], report["blockers"])
        revision = ExamGenerationService.generate(cycle_course_id=parent.id, tenant_id=self.tenant.id,
            actor=self.reviewer, expected_current_revision=0, expected_input_fingerprint=problem.input_fingerprint,
            request_token="section-set-tests-unique-request-token").revision
        return problem, report, revision

    def test_more_than_two_sections_preview_matches_saved_sets_keys_and_reprint(self):
        from .generation_reporting import GenerationAnswerKeyService
        from .questionnaire_printing import AdminQuestionnairePrintService
        parent, _policy = self.policy_course(shuffle=True)
        # Authoritative fixed-order metadata is included in the generation fingerprint.
        questions = list(Question.objects.filter(contribution__cycle_course=parent).order_by("id"))
        for index, question in enumerate(questions):
            Question.objects.filter(pk=question.pk).update(choices_fixed_order=index % 2 == 0,
                choice_a="Hydrogen", choice_b="Helium", choice_c="Lithium", choice_d="Beryllium", correct_answer="B")
        problem, preview, revision = self.generate(parent)
        self.assertEqual(revision.generated_sets.count(), 3)
        self.assertEqual(revision.section_set_assignments.count(), 3)
        self.assertEqual(preview["pairwise_overlap"], revision.set_policy_snapshot["overlap"])
        self.assertGreater(len(set(q for p in problem.section_set_plan for q in p["source_ids"])), 100)
        for plan in problem.section_set_plan:
            row = revision.generated_sets.get(set_code=plan["set_code"])
            items = list(row.items.order_by("position"))
            self.assertEqual(tuple(i.source_question_id for i in items), plan["source_ids"])
            context = GenerationAnswerKeyService.build_context(revision=revision, set_code=row.set_code)
            self.assertEqual([i["correct_answer_snapshot"] for i in context["items"]], [i.correct_answer_snapshot for i in items])
            printed = AdminQuestionnairePrintService.build_safe_context(revision=revision, set_code=row.set_code, actor=self.reviewer)
            again = AdminQuestionnairePrintService.build_safe_context(revision=revision, set_code=row.set_code, actor=self.reviewer)
            self.assertEqual(printed["items"], again["items"])
            self.assertTrue(all(i.choice_order_snapshot == [0, 1, 2, 3] for i in items if i.choices_fixed_order_snapshot))
        self.assertTrue(GeneratedExamItem.objects.filter(generated_set__generation_revision=revision,
            choices_fixed_order_snapshot=False).exclude(choice_order_snapshot=[0, 1, 2, 3]).exists())
        verify_policy_revision(revision)
        client = Client()
        client.force_login(self.reviewer)
        detail = client.get(reverse("departmental_exams:generated_revision_detail", args=[revision.id]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Saved academic section assignments")
        for printable in (False, True):
            route = "generation_selection_audit_print" if printable else "generation_selection_audit"
            report = client.get(reverse(f"departmental_exams:{route}", args=[revision.id]))
            self.assertEqual(report.status_code, 200)
            self.assertContains(report, "Saved section sets / positions")
            self.assertContains(report, "Actual pairwise question overlap")
        # Output uses frozen content even if a live source is changed out of band.
        before = printed["items"]
        Question.objects.filter(pk=items[0].source_question_id).update(question_text="Later source text")
        after = AdminQuestionnairePrintService.build_safe_context(revision=revision, set_code=row.set_code, actor=self.reviewer)
        self.assertEqual(before, after["items"])

    def test_same_questions_same_order(self):
        for mode in ("SAME_ORDER",):
            with self.subTest(mode=mode):
                parent, policy = self.policy_course(mode=mode)
                problem, report = Stage6ReadinessService.build_problem(cycle_course=parent)
                self.assertTrue(report["ready"], report["blockers"])
                sequences = [p["source_ids"] for p in problem.section_set_plan]
                self.assertTrue(all(set(ids) == set(sequences[0]) for ids in sequences))
                if mode == "SAME_ORDER":
                    self.assertTrue(all(ids == sequences[0] for ids in sequences))
                else:
                    self.assertGreater(len(set(sequences)), 1)
                self.assertTrue(all(r["percent"] == 100 for r in report["pairwise_overlap"]))

    def test_shuffled_order_same_membership(self):
        parent, _ = self.policy_course(mode="SHUFFLED_ORDER")
        problem, report = Stage6ReadinessService.build_problem(cycle_course=parent)
        sequences = [p["source_ids"] for p in problem.section_set_plan]
        self.assertTrue(all(set(ids) == set(sequences[0]) for ids in sequences))
        self.assertGreater(len(set(sequences)), 1)
        self.assertTrue(all(r["percent"] == 100 for r in report["pairwise_overlap"]))

    def test_full_overlap_is_valid_and_shortage_is_distinct(self):
        parent, policy = self.policy_course(mode="DIFFERENT")
        problem, _ = Stage6ReadinessService.build_problem(cycle_course=parent)
        retain = set(problem.section_set_plan[0]["source_ids"])
        Question.objects.filter(contribution__cycle_course=parent).exclude(id__in=retain).delete()
        _, preview, revision = self.generate(parent)
        self.assertTrue(all(p["percent"] == 100 and p["unavoidable_pool_lower_bound"] == 50 for p in preview["pairwise_overlap"]))
        verify_policy_revision(revision)
        tiny = replace(problem, blocks=problem.blocks[:1])
        with self.assertRaisesMessage(ValidationError, "cannot satisfy the blueprint quotas"):
            select_blocks(tiny, __import__("collections").Counter(), seed="shortage", budget=1000)

    def test_cross_campus_and_unassigned_set_denied(self):
        parent, _ = self.policy_course()
        _, _, revision = self.generate(parent)
        target = revision.section_set_assignments.select_related("generated_set", "offering").first()
        assignment = SimpleNamespace(offering_id=target.offering_id, offering=target.offering)
        require_faculty_section_set(revision=revision, set_code=target.generated_set.set_code,
            contribution=None, campus_id=target.campus_id_snapshot, assignments=[assignment])
        with self.assertRaises(PermissionDenied):
            require_faculty_section_set(revision=revision, set_code=target.generated_set.set_code,
                contribution=None, campus_id=target.campus_id_snapshot + 999, assignments=[assignment])
        with self.assertRaises(PermissionDenied):
            require_faculty_section_set(revision=revision, set_code=target.generated_set.set_code,
                contribution=None, campus_id=target.campus_id_snapshot, assignments=[])

    def test_policy_configuration_permission_and_lifecycle(self):
        parent, policy = self.policy_course()
        client = Client()
        client.force_login(self.manager)
        url = reverse("departmental_exams:generator_policy", args=[parent.cycle_id])
        response = client.post(url, {"expected_revision": policy.revision, "departmental": "on", "mode": "SAME_ORDER"})
        self.assertEqual(response.status_code, 409)
        policy.refresh_from_db()
        self.assertEqual(policy.mode, "DIFFERENT")
        client.force_login(self.reviewer)
        self.assertEqual(client.get(url).status_code, 403)

    def test_legacy_a_b_and_nonapplicable_classification_are_unchanged(self):
        parent, policy = self.policy_course()
        policy.departmental = False
        policy.save()
        problem, report = Stage6ReadinessService.build_problem(cycle_course=parent)
        self.assertFalse(problem.set_policy_snapshot)
        self.assertNotIn("pairwise_overlap", report)

    def test_saved_choice_or_assignment_tampering_fails_closed(self):
        parent, _ = self.policy_course()
        _, _, revision = self.generate(parent)
        item = GeneratedExamItem.objects.filter(generated_set__generation_revision=revision).first()
        GeneratedExamItem.objects.filter(pk=item.pk).update(correct_answer_snapshot="D" if item.correct_answer_snapshot != "D" else "A")
        with self.assertRaises(PermissionDenied):
            verify_policy_revision(revision)
        assignment = revision.section_set_assignments.first()
        with self.assertRaises(ValidationError):
            revision.section_set_assignments.filter(pk=assignment.pk).update(section_label_snapshot="Changed")
        from .models import ExamGenerationRevision
        from .generation_reporting import GenerationAnswerKeyService
        from .questionnaire_printing import AdminQuestionnairePrintService
        ExamGenerationRevision.objects.filter(pk=revision.pk).update(set_policy_snapshot={})
        revision.refresh_from_db()
        with self.assertRaises(PermissionDenied):
            GenerationAnswerKeyService.build_context(revision=revision, set_code=item.generated_set.set_code)
        with self.assertRaises(PermissionDenied):
            AdminQuestionnairePrintService.build_safe_context(revision=revision, set_code=item.generated_set.set_code, actor=self.reviewer)
        from .generation_reporting import GenerationSelectionAuditReportService
        with self.assertRaises(PermissionDenied):
            GenerationSelectionAuditReportService.build_context(revision=revision, filter_code="all")
        client = Client()
        client.force_login(self.reviewer)
        self.assertEqual(client.get(reverse("departmental_exams:generated_revision_detail", args=[revision.id])).status_code, 403)

    def test_infeasible_category_reports_shortage_and_saves_no_output(self):
        from .models import ExamGenerationRevision
        parent, _ = self.policy_course()
        Question.objects.filter(contribution__cycle_course=parent, difficulty="DIFFICULT").delete()
        problem, report = Stage6ReadinessService.build_problem(cycle_course=parent)
        self.assertIsNone(problem)
        self.assertFalse(report["ready"])
        self.assertTrue(any(s["label"] == "Difficult" and s["available"] == 0 for s in report["shortages"]))
        with self.assertRaises(ValidationError):
            ExamGenerationService.generate(cycle_course_id=parent.id, tenant_id=self.tenant.id,
                actor=self.reviewer, expected_current_revision=0, expected_input_fingerprint="f" * 64,
                request_token="infeasible-section-tests-request-token")
        self.assertFalse(ExamGenerationRevision.objects.exists())
        self.assertFalse(GeneratedExamSet.objects.exists())

    def test_audit_failure_rolls_back_sets_assignments_and_revision(self):
        from .models import ExamGenerationRevision, ExamSectionSetAssignment
        parent, _ = self.policy_course()
        with patch("apps.departmental_exams.generation_services.ExamGenerationService._audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaisesMessage(RuntimeError, "audit unavailable"):
                self.generate(parent)
        self.assertFalse(ExamGenerationRevision.objects.exists())
        self.assertFalse(GeneratedExamSet.objects.exists())
        self.assertFalse(ExamSectionSetAssignment.objects.exists())

    def test_automatic_preferred_mix_shortage_remains_valid_with_warning(self):
        from .automatic_generation_audit import AutomaticGenerationAuditService
        from .models import ExaminationCycle
        parent, _ = self.policy_course()
        parent.cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        parent.cycle.save(update_fields=["processing_mode"])
        parent.refresh_from_db()
        problem, _ = Stage6ReadinessService.build_problem(cycle_course=parent)
        retained = problem.section_set_plan[0]["source_ids"]
        Question.objects.filter(contribution__cycle_course=parent).exclude(pk__in=retained).delete()
        Question.objects.filter(contribution__cycle_course=parent).update(difficulty="EASY")
        problem, report = Stage6ReadinessService.build_problem(cycle_course=parent)
        self.assertTrue(report["ready"], report["blockers"])
        self.assertTrue(all(p["percent"] == 100 for p in report["pairwise_overlap"]))
        revision = ExamGenerationService.generate(cycle_course_id=parent.id, tenant_id=self.tenant.id,
            actor=None, generation_trigger="AUTOMATIC", expected_current_revision=0,
            expected_input_fingerprint=problem.input_fingerprint, request_token="soft-mix-section-tests-request-token").revision
        findings, _ = AutomaticGenerationAuditService._build_findings(revision=revision)
        self.assertTrue(any(f["status"] == "WARNING" for f in findings))
        self.assertFalse(any(f["status"] == "FAIL" for f in findings))

    def test_whole_passages_and_manual_lock_use_exact_saved_evidence(self):
        from .models import ExamScenario, ExamScenarioMember
        from .approval_services import ExamApprovalLockService
        parent, _ = self.policy_course(shuffle=True)
        questions = list(Question.objects.filter(contribution__cycle_course=parent).order_by("id")[:2])
        case = ExamScenario.objects.create(blueprint=parent.exam_blueprint, title="Shared passage",
            stimulus="Read this shared passage once.", created_by=self.reviewer, updated_by=self.reviewer)
        for index, question in enumerate(questions, 1):
            ExamScenarioMember.objects.create(scenario=case, question=question, position=index)
        _, _, revision = self.generate(parent)
        for row in revision.generated_sets.all():
            members = list(row.items.filter(scenario_id_snapshot=case.id).order_by("position"))
            if members:
                self.assertEqual(len(members), 2)
                self.assertEqual([m.scenario_member_position_snapshot for m in members], [1, 2])
                self.assertEqual(members[1].position, members[0].position + 1)
                self.assertEqual(members[0].scenario_stimulus_snapshot, case.stimulus)
        result = ExamApprovalLockService.approve_and_lock(revision_id=revision.id, tenant_id=self.tenant.id,
            actor=self.reviewer, expected_revision_number=revision.revision_number,
            expected_source_input_fingerprint=revision.source_input_fingerprint)
        self.assertEqual(result.revision.status, "LOCKED")
        with self.assertRaises(ValidationError):
            result.revision.set_policy_snapshot = {}
            result.revision.save()
        from importlib import import_module
        from django.apps import apps
        from django.db import connection
        from django.db.migrations.exceptions import IrreversibleError
        migration = import_module("apps.departmental_exams.migrations.0037_exam_generator_set_modes")
        with self.assertRaises(IrreversibleError):
            migration.preserve_saved_section_sets(apps, SimpleNamespace(connection=connection))

    def test_standardized_release_reprint_key_and_personalized_section_assignment(self):
        from django.utils import timezone
        from .models import CourseExamConfiguration, ExaminationCycle, FacultyContribution
        from .questionnaire_printing import QuestionnairePrintReleaseService, FacultyQuestionnairePrintService
        from .answer_key_release import AnswerKeyReleaseService, FacultyAnswerKeyReleaseService
        from .personalized_answer_sheets import PersonalizedAnswerSheetService
        from apps.enrollment.models import Enrollment
        from apps.students.models import Student
        from .automatic_generation_audit import AutomaticGenerationAuditService
        parent, _ = self.policy_course(shuffle=True)
        with _classification_service_scope():
            CycleCourse.objects.filter(pk=parent.pk).update(exam_classification="STANDARDIZED")
        parent.cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        parent.cycle.save(update_fields=["processing_mode"])
        parent.refresh_from_db()
        problem, preview = Stage6ReadinessService.build_problem(cycle_course=parent)
        self.assertTrue(preview["ready"], preview["blockers"])
        revision = ExamGenerationService.generate(cycle_course_id=parent.id, tenant_id=self.tenant.id,
            actor=None, generation_trigger="AUTOMATIC", expected_current_revision=0,
            expected_input_fingerprint=problem.input_fingerprint, request_token="automatic-section-tests-request-token").revision
        assignment = revision.section_set_assignments.select_related("generated_set", "offering__section").first()
        contribution = FacultyContribution.objects.select_related("faculty_user", "cycle_course__cycle").get(
            cycle_course=parent, source_campus_id=assignment.campus_id_snapshot)
        faculty = contribution.faculty_user
        now = timezone.now()
        release = QuestionnairePrintReleaseService.release(cycle_course_id=parent.id, revision_id=revision.id,
            tenant_id=self.tenant.id, actor=self.admin, target_campus_id=assignment.campus_id_snapshot,
            print_from=now-timezone.timedelta(minutes=1), print_until=now+timezone.timedelta(hours=1))
        kwargs = dict(contribution=contribution, release_id=release.id, set_code=assignment.generated_set.set_code, actor=faculty)
        first = FacultyQuestionnairePrintService.build_safe_context(**kwargs)
        second = FacultyQuestionnairePrintService.build_safe_context(**kwargs)
        self.assertEqual(first["items"], second["items"])
        foreign = revision.section_set_assignments.exclude(pk=assignment.pk).select_related("generated_set").first()
        with self.assertRaises(PermissionDenied):
            FacultyQuestionnairePrintService.build_safe_context(**{**kwargs, "set_code": foreign.generated_set.set_code})
        key_release = AnswerKeyReleaseService.release(cycle_course_id=parent.id, revision_id=revision.id,
            tenant_id=self.tenant.id, actor=self.admin, target_campus_id=assignment.campus_id_snapshot,
            recipient_course_id=parent.id, available_from=now-timezone.timedelta(minutes=1),
            available_until=now+timezone.timedelta(hours=1), attestation_confirmed=True)
        key = FacultyAnswerKeyReleaseService.build_safe_context(contribution=contribution, release_id=key_release.id,
            set_code=assignment.generated_set.set_code, actor=faculty, printable=False)
        self.assertEqual([i["correct_answer"] for i in key["items"]],
                         list(assignment.generated_set.items.order_by("position").values_list("correct_answer_snapshot", flat=True)))
        with self.assertRaises(PermissionDenied):
            FacultyAnswerKeyReleaseService.build_safe_context(contribution=contribution, release_id=key_release.id,
                set_code=foreign.generated_set.set_code, actor=faculty, printable=False)
        # Persistent student sheets resolve to this offering's exact saved set.
        offering = assignment.offering
        student = Student.objects.create(tenant=self.tenant, campus=offering.campus, department=offering.department,
            program=offering.program, student_no="SET-MODE-1", first_name="Section", last_name="Student")
        Enrollment.objects.create(student=student, course_offering=offering, tenant=self.tenant,
            campus=offering.campus, academic_year=offering.academic_year, term=offering.term)
        PersonalizedAnswerSheetService.prepare(contribution=contribution, release_id=release.id,
            offering_id=offering.id, actor=faculty)
        self.assertEqual(revision.personalized_answer_sheet_assignments.get().set_code, assignment.generated_set.set_code)
        findings, counts = AutomaticGenerationAuditService._build_findings(revision=revision)
        self.assertTrue(all(f["status"] == "PASS" for f in findings))
        self.assertEqual(counts["section_set_count"], 3)

    def test_new_policy_both_applicabilities_stale_state_and_direct_deny(self):
        from apps.rbac.models import Permission
        cycle = self.make_cycle()
        client = Client()
        client.force_login(self.manager)
        url = reverse("departmental_exams:generator_policy", args=[cycle.id])
        data = {"expected_revision": 0, "departmental": "on", "standardized": "on",
                "mode": "SHUFFLED_ORDER", "shuffle_choices": "on"}
        self.assertEqual(client.post(url, data).status_code, 302)
        policy = ExamGeneratorPolicy.objects.get(cycle=cycle)
        self.assertTrue(policy.departmental and policy.standardized and policy.shuffle_choices)
        self.assertEqual(client.post(url, data).status_code, 409)
        UserPermission.objects.create(user=self.manager, tenant=self.tenant,
            permission=Permission.objects.get(code="departmental_exams.manage_cycles"), grant_type="DENY")
        self.assertEqual(client.get(url).status_code, 403)


from .tests_faculty_cases import FacultyCaseFixtureMixin
from . import tests_case_generation as case_generation_fixtures


class AutomaticCaseSectionSetTests(FacultyCaseFixtureMixin, Stage4TestCase):
    fill_and_submit = case_generation_fixtures.CaseGenerationTests.fill_and_submit
    generate = case_generation_fixtures.CaseGenerationTests.generate

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from apps.rbac.models import Permission
        Permission.objects.get_or_create(code="faculty_portal.access", defaults={
            "module": "faculty_portal", "action": "access", "description": "Faculty access"})

    def make_cycle(self, **kwargs):
        self.configurer = self.admin
        cycle = super().make_cycle(**kwargs)
        cycle.processing_mode = "AUTOMATIC_GENERATION"
        cycle.automatic_contributor_completion_policy = "SUFFICIENT_POOL"
        cycle.save()
        return cycle

    def make_course(self, **kwargs):
        from .setup_services import CourseSetupService
        parent = super().make_course(**kwargs)
        CourseSetupService.classify(course_id=parent.id, tenant_id=self.tenant.id, actor=self.admin,
            classification="DEPARTMENTAL", expected_state=CourseSetupService.fingerprint(parent))
        parent.refresh_from_db()
        return parent

    def test_automatic_case_detail_reprint_and_key_use_policy_digest(self):
        from .generation_reporting import GenerationAnswerKeyService
        from .questionnaire_printing import AdminQuestionnairePrintService
        from .models import ExamScenario
        ExamGeneratorPolicy.objects.create(cycle=self.cycle, departmental=True, shuffle_choices=True)
        cases = self.fill_and_submit()
        revision = self.generate()
        self.assertEqual(revision.algorithm_version, "automatic-case-v2")
        row = revision.generated_sets.get()
        self.assertEqual(row.items.filter(scenario_id_snapshot__isnull=False).count(), 50)
        verify_policy_revision(revision)
        self.client.force_login(self.admin)
        response = self.client.get(reverse("departmental_exams:generated_revision_detail", args=[revision.id]))
        self.assertEqual(response.status_code, 200)
        before = AdminQuestionnairePrintService.build_safe_context(revision=revision, set_code=row.set_code, actor=self.admin)
        key = GenerationAnswerKeyService.build_context(revision=revision, set_code=row.set_code)
        self.assertEqual([i["correct_answer_snapshot"] for i in key["items"]],
                         list(row.items.order_by("position").values_list("correct_answer_snapshot", flat=True)))
        ExamScenario.objects.filter(pk=cases[0].pk).update(stimulus="Later live narrative")
        after = AdminQuestionnairePrintService.build_safe_context(revision=revision, set_code=row.set_code, actor=self.admin)
        self.assertEqual(before["items"], after["items"])
