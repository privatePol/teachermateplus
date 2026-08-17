from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.auditlog.models import AuditLog

from .generation_algorithms import (
    IdentitySelectionResult,
    proportional_campus_difficulty_score,
)
from .generation_readiness import Stage6ReadinessService
from .generation_services import (
    ExamGenerationService,
    GenerationConflict,
    GenerationLimitExceeded,
)
from .models import (
    ExamGenerationRevision,
    GeneratedExamItem,
    GeneratedExamSet,
    Question,
)
from .stage4_test_support import Stage4TestCase
from .tests_stage6_blueprint_readiness import Stage6BlueprintFixtureMixin


class Stage6BGenerationFixtureMixin(Stage6BlueprintFixtureMixin):
    GENERATION_SUCCESS_ACTIONS = (
        "DE_EXAM_GENERATED",
        "DE_EXAM_REGENERATED",
        "DE_EXAM_GENERATION_SUPERSEDED",
    )

    def ready_generation_course(self):
        parent, _configuration, _contributions, _assignments = self.closed_course()
        self.no_sections_blueprint(parent)
        problem, readiness = Stage6ReadinessService.build_problem(cycle_course=parent)
        self.assertTrue(readiness["ready"], readiness["blockers"])
        self.assertIsNotNone(problem)
        return parent, problem

    @staticmethod
    def close_cycle(parent):
        cycle = parent.cycle
        cycle.status = cycle.Status.CLOSED
        cycle.save(update_fields=["status", "updated_at"])

    def assert_no_generation_output(self):
        self.assertFalse(ExamGenerationRevision.objects.exists())
        self.assertFalse(GeneratedExamSet.objects.exists())
        self.assertFalse(GeneratedExamItem.objects.exists())
        self.assertFalse(
            AuditLog.objects.filter(action__in=self.GENERATION_SUCCESS_ACTIONS).exists()
        )

    @staticmethod
    def proved_selection(problem):
        buckets = {}
        for block in problem.blocks:
            member = block.members[0]
            buckets.setdefault((member.campus, member.difficulty), []).append(block)
        target_cells = {
            ("CUBAO", "EASY"): 5,
            ("CUBAO", "MODERATE"): 9,
            ("CUBAO", "DIFFICULT"): 3,
            ("FAIRVIEW", "EASY"): 5,
            ("FAIRVIEW", "MODERATE"): 8,
            ("FAIRVIEW", "DIFFICULT"): 3,
            ("TAYTAY", "EASY"): 5,
            ("TAYTAY", "MODERATE"): 8,
            ("TAYTAY", "DIFFICULT"): 4,
        }
        set_a = []
        set_b = []
        for cell, amount in target_cells.items():
            rows = sorted(buckets[cell], key=lambda block: block.block_id)
            set_a.extend(rows[:amount])
            set_b.extend(rows[amount : amount * 2])
        cell_counts = dict(target_cells)
        proportional = 2 * proportional_campus_difficulty_score(
            total=problem.final_count,
            campus_quotas=problem.campus_quotas,
            difficulty_quotas=problem.difficulty_quotas,
            cell_counts=cell_counts,
        )
        appearances = {}
        for block in set_a + set_b:
            for member in block.members:
                appearances[member.contributor_id] = appearances.get(member.contributor_id, 0) + 1
        return IdentitySelectionResult(
            feasible=True,
            limit_hit=False,
            states_explored=321,
            set_a_block_ids=tuple(block.block_id for block in set_a),
            set_b_block_ids=tuple(block.block_id for block in set_b),
            overlap=problem.minimum_overlap,
            proportional_score=proportional,
            contributors_represented=len(appearances),
            squared_contributor_concentration=sum(
                count * count for count in appearances.values()
            ),
        )

    def generate_with_proved_selection(
        self,
        *,
        parent,
        problem,
        token="t" * 40,
        expected_revision=0,
        regeneration=False,
        reason="",
    ):
        with patch(
            "apps.departmental_exams.generation_services.solve_identity_aware_two_sets",
            return_value=self.proved_selection(problem),
        ):
            return ExamGenerationService.generate(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                actor=self.reviewer,
                expected_current_revision=expected_revision,
                expected_input_fingerprint=problem.input_fingerprint,
                request_token=token,
                regeneration=regeneration,
                regeneration_reason=reason,
            )


class Stage6BGenerationServiceTests(Stage6BGenerationFixtureMixin, Stage4TestCase):
    def test_open_cycle_remains_ready_and_first_generation_succeeds(self):
        parent, problem = self.ready_generation_course()

        readiness = Stage6ReadinessService.evaluate(cycle_course=parent)
        self.assertTrue(readiness["ready"], readiness["blockers"])
        outcome = self.generate_with_proved_selection(parent=parent, problem=problem)

        self.assertEqual(outcome.revision.revision_number, 1)
        self.assertEqual(ExamGenerationRevision.objects.count(), 1)
        self.assertEqual(GeneratedExamSet.objects.count(), 2)
        self.assertEqual(GeneratedExamItem.objects.count(), 100)

    def test_closed_cycle_blocks_readiness_and_direct_first_generation(self):
        parent, open_problem = self.ready_generation_course()
        self.close_cycle(parent)

        problem, readiness = Stage6ReadinessService.build_problem(cycle_course=parent)
        lifecycle_blocker = next(
            item for item in readiness["blockers"] if item["code"] == "CYCLE_NOT_OPEN"
        )
        self.assertIsNone(problem)
        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["status"], "BLOCKED")
        self.assertEqual(
            lifecycle_blocker["message"],
            "The examination cycle is not open for Stage 6 work.",
        )

        with self.assertRaisesRegex(GenerationConflict, "cycle is not open"):
            ExamGenerationService.generate(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                actor=self.reviewer,
                expected_current_revision=0,
                expected_input_fingerprint=open_problem.input_fingerprint,
                request_token="c" * 40,
            )

        self.assert_no_generation_output()

    def test_closed_cycle_rejects_regeneration_without_mutating_current_revision(self):
        parent, problem = self.ready_generation_course()
        current = self.generate_with_proved_selection(parent=parent, problem=problem).revision
        revision_before = (
            current.status,
            current.current_marker,
            current.supersedes_id,
            current.updated_at,
        )
        sets_before = list(
            GeneratedExamSet.objects.order_by("set_code").values_list(
                "id", "set_code", "item_count"
            )
        )
        items_before = list(
            GeneratedExamItem.objects.order_by("generated_set_id", "position").values_list(
                "id",
                "generated_set_id",
                "position",
                "source_question_id",
                "correct_answer_snapshot",
            )
        )
        success_audits_before = AuditLog.objects.filter(
            action__in=self.GENERATION_SUCCESS_ACTIONS
        ).count()
        self.close_cycle(parent)

        with self.assertRaisesRegex(GenerationConflict, "cycle is not open"):
            ExamGenerationService.generate(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                actor=self.reviewer,
                expected_current_revision=1,
                expected_input_fingerprint=problem.input_fingerprint,
                request_token="r" * 40,
                regeneration=True,
                regeneration_reason="Reviewer requested a new confidential arrangement.",
            )

        current.refresh_from_db()
        self.assertEqual(
            (
                current.status,
                current.current_marker,
                current.supersedes_id,
                current.updated_at,
            ),
            revision_before,
        )
        self.assertEqual(ExamGenerationRevision.objects.count(), 1)
        self.assertEqual(
            list(
                GeneratedExamSet.objects.order_by("set_code").values_list(
                    "id", "set_code", "item_count"
                )
            ),
            sets_before,
        )
        self.assertEqual(
            list(
                GeneratedExamItem.objects.order_by(
                    "generated_set_id", "position"
                ).values_list(
                    "id",
                    "generated_set_id",
                    "position",
                    "source_question_id",
                    "correct_answer_snapshot",
                )
            ),
            items_before,
        )
        self.assertEqual(
            AuditLog.objects.filter(action__in=self.GENERATION_SUCCESS_ACTIONS).count(),
            success_audits_before,
        )

    def test_real_selector_completes_representative_ready_course(self):
        parent, problem = self.ready_generation_course()
        outcome = ExamGenerationService.generate(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.reviewer,
            expected_current_revision=0,
            expected_input_fingerprint=problem.input_fingerprint,
            request_token="p" * 40,
        )
        revision = outcome.revision
        self.assertEqual(revision.minimum_overlap, 0)
        self.assertEqual(revision.generated_sets.count(), 2)
        self.assertEqual(
            list(revision.generated_sets.order_by("set_code").values_list("item_count", flat=True)),
            [50, 50],
        )

    def test_generation_persists_two_complete_immutable_sets_and_content_safe_audit(self):
        parent, problem = self.ready_generation_course()
        outcome = self.generate_with_proved_selection(parent=parent, problem=problem)
        revision = outcome.revision
        self.assertFalse(outcome.reused)
        self.assertEqual(revision.revision_number, 1)
        self.assertEqual(revision.current_marker, 1)
        self.assertEqual(revision.request_token_digest, ExamGenerationService.request_token_digest("t" * 40))
        self.assertNotIn("t" * 40, str(revision.__dict__))
        sets = list(revision.generated_sets.order_by("set_code"))
        self.assertEqual([row.set_code for row in sets], ["A", "B"])
        self.assertEqual([row.items.count() for row in sets], [50, 50])
        for generated_set in sets:
            self.assertEqual(
                list(generated_set.items.order_by("position").values_list("position", flat=True)),
                list(range(1, 51)),
            )
            self.assertEqual(
                generated_set.items.values("source_question_id").distinct().count(),
                50,
            )
        item = sets[0].items.order_by("position").first()
        original_text = item.question_text_snapshot
        Question.objects.filter(pk=item.source_question_id).update(
            question_text="Changed after immutable generation"
        )
        item.refresh_from_db()
        self.assertEqual(item.question_text_snapshot, original_text)
        item.question_text_snapshot = "Attempted rewrite"
        with self.assertRaisesRegex(ValidationError, "immutable"):
            item.save()
        audit = AuditLog.objects.get(action="DE_EXAM_GENERATED")
        rendered = str(audit.metadata_json)
        self.assertNotIn(original_text, rendered)
        self.assertNotIn("correct_answer", rendered)
        self.assertNotIn("hmac", rendered.lower())

    def test_generated_snapshots_preserve_scientific_notation_exactly(self):
        parent, _problem = self.ready_generation_course()
        questions = list(
            Question.objects.filter(contribution__cycle_course=parent).order_by("id")
        )
        for question in questions:
            question.question_text = rf"\(\frac{{x_{{{question.id}}}^2}}{{\sqrt{{y}}}}\)"
            question.choice_a = r"\(\alpha + \theta\)"
            question.choice_b = r"\(\sum_{i=1}^{n} i\)"
            question.choice_c = r"\(\int_0^1 x\,dx\)"
            question.choice_d = r"\(\ce{2H2 + O2 -> 2H2O}\)"
        Question.objects.bulk_update(
            questions,
            ["question_text", "choice_a", "choice_b", "choice_c", "choice_d"],
        )
        problem, readiness = Stage6ReadinessService.build_problem(cycle_course=parent)
        self.assertTrue(readiness["ready"], readiness["blockers"])
        outcome = self.generate_with_proved_selection(parent=parent, problem=problem)
        item = GeneratedExamItem.objects.filter(
            generated_set__generation_revision=outcome.revision
        ).select_related("source_question").first()
        source = item.source_question
        self.assertEqual(item.question_text_snapshot, source.question_text)
        self.assertEqual(
            item.choices_snapshot,
            [source.choice_a, source.choice_b, source.choice_c, source.choice_d],
        )

    def test_duplicate_token_reuses_revision_before_stale_revision_check(self):
        parent, problem = self.ready_generation_course()
        first = self.generate_with_proved_selection(parent=parent, problem=problem)
        duplicate = ExamGenerationService.generate(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.reviewer,
            expected_current_revision=0,
            expected_input_fingerprint=problem.input_fingerprint,
            request_token="t" * 40,
        )
        self.assertTrue(duplicate.reused)
        self.assertEqual(duplicate.revision.id, first.revision.id)
        self.assertEqual(ExamGenerationRevision.objects.count(), 1)

    def test_stale_revision_and_fingerprint_create_nothing(self):
        parent, problem = self.ready_generation_course()
        with self.assertRaisesRegex(GenerationConflict, "revision changed"):
            ExamGenerationService.generate(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                actor=self.reviewer,
                expected_current_revision=9,
                expected_input_fingerprint=problem.input_fingerprint,
                request_token="r" * 40,
            )
        with self.assertRaisesRegex(GenerationConflict, "inputs changed"):
            ExamGenerationService.generate(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                actor=self.reviewer,
                expected_current_revision=0,
                expected_input_fingerprint="0" * 64,
                request_token="f" * 40,
            )
        self.assertFalse(ExamGenerationRevision.objects.exists())
        self.assertFalse(GeneratedExamSet.objects.exists())
        self.assertFalse(GeneratedExamItem.objects.exists())

    def test_regeneration_supersedes_current_and_preserves_old_snapshots(self):
        parent, problem = self.ready_generation_course()
        first = self.generate_with_proved_selection(parent=parent, problem=problem).revision
        first_snapshot = list(
            first.generated_sets.get(set_code="A").items.order_by("position").values_list(
                "source_question_id", "question_text_snapshot", "correct_answer_snapshot"
            )
        )
        refreshed_problem, readiness = Stage6ReadinessService.build_problem(cycle_course=parent)
        self.assertTrue(readiness["ready"])
        second = self.generate_with_proved_selection(
            parent=parent,
            problem=refreshed_problem,
            token="n" * 40,
            expected_revision=1,
            regeneration=True,
            reason="Rebalance the confidential generation after formal reviewer review.",
        ).revision
        first.refresh_from_db()
        self.assertEqual(first.status, ExamGenerationRevision.Status.SUPERSEDED)
        self.assertIsNone(first.current_marker)
        self.assertEqual(second.supersedes_id, first.id)
        self.assertEqual(second.revision_number, 2)
        self.assertEqual(
            ExamGenerationRevision.objects.filter(cycle_course=parent, current_marker=1).count(),
            1,
        )
        self.assertEqual(
            list(
                first.generated_sets.get(set_code="A").items.order_by("position").values_list(
                    "source_question_id", "question_text_snapshot", "correct_answer_snapshot"
                )
            ),
            first_snapshot,
        )
        self.assertTrue(AuditLog.objects.filter(action="DE_EXAM_REGENERATED").exists())
        superseded_audit = AuditLog.objects.get(action="DE_EXAM_GENERATION_SUPERSEDED")
        self.assertNotIn("Rebalance the confidential", str(superseded_audit.metadata_json))

    def test_limit_and_audit_failure_roll_back_without_partial_output(self):
        parent, problem = self.ready_generation_course()
        with patch(
            "apps.departmental_exams.generation_services.solve_identity_aware_two_sets",
            return_value=IdentitySelectionResult(False, True, 5),
        ), self.assertRaises(GenerationLimitExceeded):
            ExamGenerationService.generate(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                actor=self.reviewer,
                expected_current_revision=0,
                expected_input_fingerprint=problem.input_fingerprint,
                request_token="l" * 40,
            )
        self.assertFalse(ExamGenerationRevision.objects.exists())

        with patch(
            "apps.departmental_exams.generation_services.solve_identity_aware_two_sets",
            return_value=self.proved_selection(problem),
        ), patch(
            "apps.departmental_exams.generation_services.AuditService.log_event",
            side_effect=RuntimeError("audit unavailable"),
        ), self.assertRaisesRegex(RuntimeError, "audit unavailable"):
            ExamGenerationService.generate(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                actor=self.reviewer,
                expected_current_revision=0,
                expected_input_fingerprint=problem.input_fingerprint,
                request_token="a" * 40,
            )
        self.assertFalse(ExamGenerationRevision.objects.exists())
        self.assertFalse(GeneratedExamSet.objects.exists())
        self.assertFalse(GeneratedExamItem.objects.exists())


class Stage6BGenerationViewTests(Stage6BGenerationFixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_workspace_is_reviewer_only_and_has_honest_busy_markup(self):
        parent, _problem = self.ready_generation_course()
        url = reverse("departmental_exams:generation_workspace", args=[parent.id])
        self.client.force_login(self.configurer)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.reviewer)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Processing Set A and Set B")
        self.assertContains(response, 'role="progressbar"')
        self.assertContains(response, 'aria-busy="false"')
        self.assertContains(response, "data-generation-form")
        self.assertNotContains(response, "% complete")

    def test_stale_post_is_409_and_detail_uses_historical_snapshots(self):
        parent, problem = self.ready_generation_course()
        self.client.force_login(self.reviewer)
        stale = self.client.post(
            reverse("departmental_exams:generate_exam", args=[parent.id]),
            {
                "expected_current_revision": 7,
                "input_fingerprint": problem.input_fingerprint,
                "request_token": "s" * 40,
            },
        )
        self.assertEqual(stale.status_code, 409)
        self.assertFalse(ExamGenerationRevision.objects.exists())

        revision = self.generate_with_proved_selection(parent=parent, problem=problem).revision
        item = revision.generated_sets.get(set_code="A").items.order_by("position").first()
        detail = self.client.get(
            reverse("departmental_exams:generated_revision_detail", args=[revision.id])
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, item.question_text_snapshot)
        self.assertContains(detail, "Correct answer:")
        self.assertContains(detail, "SET A")
        self.assertContains(detail, "SET B")
        self.client.force_login(self.configurer)
        self.assertEqual(
            self.client.get(
                reverse("departmental_exams:generated_revision_detail", args=[revision.id])
            ).status_code,
            403,
        )

    def test_direct_first_generation_post_while_closed_is_safe_409(self):
        parent, problem = self.ready_generation_course()
        confidential_text = Question.objects.filter(
            contribution__cycle_course=parent
        ).values_list("question_text", flat=True).first()
        self.close_cycle(parent)
        self.client.force_login(self.reviewer)

        response = self.client.post(
            reverse("departmental_exams:generate_exam", args=[parent.id]),
            {
                "expected_current_revision": 0,
                "input_fingerprint": problem.input_fingerprint,
                "request_token": "d" * 40,
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "cycle is not open", status_code=409)
        self.assertNotContains(response, confidential_text, status_code=409)
        self.assert_no_generation_output()

    def test_open_get_then_closed_cycle_first_generation_post_is_rejected(self):
        parent, _problem = self.ready_generation_course()
        self.client.force_login(self.reviewer)
        workspace = self.client.get(
            reverse("departmental_exams:generation_workspace", args=[parent.id])
        )
        self.assertEqual(workspace.status_code, 200)
        initial = workspace.context["generation_form"].initial
        self.close_cycle(parent)

        response = self.client.post(
            reverse("departmental_exams:generate_exam", args=[parent.id]),
            {
                "expected_current_revision": initial["expected_current_revision"],
                "input_fingerprint": initial["input_fingerprint"],
                "request_token": initial["request_token"],
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "cycle is not open", status_code=409)
        self.assert_no_generation_output()

    def test_open_get_then_closed_cycle_regeneration_post_preserves_current(self):
        parent, problem = self.ready_generation_course()
        current = self.generate_with_proved_selection(parent=parent, problem=problem).revision
        self.client.force_login(self.reviewer)
        workspace = self.client.get(
            reverse("departmental_exams:generation_workspace", args=[parent.id])
        )
        self.assertEqual(workspace.status_code, 200)
        initial = workspace.context["regeneration_form"].initial
        item_ids_before = list(
            GeneratedExamItem.objects.order_by("generated_set_id", "position").values_list(
                "id", flat=True
            )
        )
        self.close_cycle(parent)

        response = self.client.post(
            reverse("departmental_exams:regenerate_exam", args=[parent.id]),
            {
                "expected_current_revision": initial["expected_current_revision"],
                "input_fingerprint": initial["input_fingerprint"],
                "request_token": initial["request_token"],
                "reason": "Reviewer requested a new confidential arrangement.",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "cycle is not open", status_code=409)
        current.refresh_from_db()
        self.assertEqual(current.status, ExamGenerationRevision.Status.GENERATED)
        self.assertEqual(current.current_marker, 1)
        self.assertEqual(ExamGenerationRevision.objects.count(), 1)
        self.assertEqual(
            list(
                GeneratedExamItem.objects.order_by(
                    "generated_set_id", "position"
                ).values_list("id", flat=True)
            ),
            item_ids_before,
        )
        self.assertFalse(AuditLog.objects.filter(action="DE_EXAM_REGENERATED").exists())
        self.assertFalse(
            AuditLog.objects.filter(action="DE_EXAM_GENERATION_SUPERSEDED").exists()
        )
