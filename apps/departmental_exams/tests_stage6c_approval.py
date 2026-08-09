import hashlib
import json
import re
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.test import Client
from django.urls import reverse

from apps.auditlog.models import AuditLog
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, UserPermission

from .approval_services import (
    ApprovalConflict,
    ExamApprovalLockService,
    GeneratedExamIntegrityService,
)
from .blueprint_services import (
    BlueprintMutationService,
    QuestionPlacementService,
    ScenarioMutationService,
    Stage6Conflict,
)
from .generation_readiness import Stage6ReadinessService
from .generation_services import ExamGenerationService, GenerationConflict
from .models import (
    ExamBlueprint,
    ExamGenerationRevision,
    ExamScenario,
    GeneratedExamItem,
    GeneratedExamSet,
    Question,
)
from .services import CourseExamConfigurationService
from .stage4_test_support import Stage4TestCase
from .tests_stage6_generation import Stage6BGenerationFixtureMixin


class Stage6CFixtureMixin(Stage6BGenerationFixtureMixin):
    APPROVAL_ACTION = "DE_EXAM_APPROVED_LOCKED"

    def generated_course(self):
        parent, problem = self.ready_generation_course()
        revision = self.generate_with_proved_selection(
            parent=parent,
            problem=problem,
        ).revision
        return parent, problem, revision

    def approve(self, revision, *, actor=None, fingerprint=None, number=None):
        return ExamApprovalLockService.approve_and_lock(
            revision_id=revision.id,
            tenant_id=self.tenant.id,
            actor=actor or self.reviewer,
            expected_revision_number=(
                revision.revision_number if number is None else number
            ),
            expected_source_input_fingerprint=(
                revision.source_input_fingerprint
                if fingerprint is None
                else fingerprint
            ),
        )

    @staticmethod
    def output_snapshot(revision):
        return list(
            GeneratedExamItem.objects.filter(
                generated_set__generation_revision=revision
            )
            .order_by("generated_set__set_code", "position")
            .values_list(
                "generated_set__set_code",
                "position",
                "source_question_id",
                "question_text_snapshot",
                "choices_snapshot",
                "correct_answer_snapshot",
                "scenario_id_snapshot",
                "scenario_stimulus_snapshot",
                "scenario_member_position_snapshot",
            )
        )

    @staticmethod
    def approval_post_data(revision):
        return {
            "expected_revision_number": revision.revision_number,
            "expected_source_input_fingerprint": revision.source_input_fingerprint,
            "set_a_reviewed": "on",
            "set_b_reviewed": "on",
            "answer_keys_reviewed": "on",
            "sections_scenarios_reviewed": "on",
            "permanent_lock_acknowledged": "on",
        }

    @staticmethod
    def snapshot_digest(item):
        payload = json.dumps(
            {
                "source_id": item.source_question_id,
                "revision": item.source_question_revision,
                "question_text": item.question_text_snapshot,
                "choices": item.choices_snapshot,
                "correct_answer": item.correct_answer_snapshot,
                "difficulty": item.difficulty_snapshot,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def assert_revision_remains_unlocked(self, revision):
        revision.refresh_from_db()
        self.assertEqual(revision.status, ExamGenerationRevision.Status.GENERATED)
        self.assertEqual(revision.current_marker, 1)
        self.assertIsNone(revision.locked_at)
        self.assertIsNone(revision.locked_by_id)
        self.assertEqual(revision.approval_attestation_version, "")
        self.assertFalse(AuditLog.objects.filter(action=self.APPROVAL_ACTION).exists())


class Stage6CApprovalServiceTests(Stage6CFixtureMixin, Stage4TestCase):
    def test_generated_transitions_atomically_to_locked_with_one_safe_audit(self):
        _parent, _problem, revision = self.generated_course()
        before = self.output_snapshot(revision)

        outcome = self.approve(revision)

        self.assertFalse(outcome.reused)
        revision.refresh_from_db()
        self.assertEqual(revision.status, ExamGenerationRevision.Status.LOCKED)
        self.assertEqual(revision.current_marker, 1)
        self.assertEqual(revision.locked_by, self.reviewer)
        self.assertIsNotNone(revision.locked_at)
        self.assertEqual(revision.approval_attestation_version, "stage6c-v1")
        self.assertEqual(self.output_snapshot(revision), before)
        revision.status = ExamGenerationRevision.Status.GENERATED
        with self.assertRaisesRegex(ValidationError, "transition"):
            revision.save()
        revision.refresh_from_db()
        revision.locked_by = self.configurer
        with self.assertRaisesRegex(ValidationError, "metadata is immutable"):
            revision.save()
        revision.refresh_from_db()
        revision.save(update_fields=["updated_at"])
        audit = AuditLog.objects.get(action=self.APPROVAL_ACTION)
        self.assertEqual(audit.actor_user_id, self.reviewer.id)
        rendered = str(audit.metadata_json)
        self.assertIn(revision.source_input_fingerprint, rendered)
        self.assertNotIn(before[0][3], rendered)
        self.assertNotIn("correct_answer", rendered)
        self.assertNotIn("request_token", rendered)
        self.assertNotIn("hmac", rendered.lower())

    def test_exact_duplicate_is_idempotent_after_cycle_close_without_second_audit(self):
        parent, _problem, revision = self.generated_course()
        first = self.approve(revision)
        parent.cycle.status = parent.cycle.Status.CLOSED
        parent.cycle.save(update_fields=["status", "updated_at"])

        second = self.approve(revision)

        self.assertFalse(first.reused)
        self.assertTrue(second.reused)
        self.assertEqual(second.revision.id, revision.id)
        self.assertEqual(AuditLog.objects.filter(action=self.APPROVAL_ACTION).count(), 1)

    def test_audit_failure_rolls_back_transition_and_all_lock_metadata(self):
        _parent, _problem, revision = self.generated_course()
        with patch(
            "apps.departmental_exams.approval_services.AuditService.log_event",
            side_effect=RuntimeError("audit unavailable"),
        ), self.assertRaisesRegex(RuntimeError, "audit unavailable"):
            self.approve(revision)

        revision.refresh_from_db()
        self.assertEqual(revision.status, ExamGenerationRevision.Status.GENERATED)
        self.assertEqual(revision.current_marker, 1)
        self.assertIsNone(revision.locked_at)
        self.assertIsNone(revision.locked_by_id)
        self.assertEqual(revision.approval_attestation_version, "")
        self.assertFalse(AuditLog.objects.filter(action=self.APPROVAL_ACTION).exists())

    def test_stale_browser_live_drift_and_regenerated_target_fail_closed(self):
        parent, problem, revision = self.generated_course()
        with self.assertRaisesRegex(ApprovalConflict, "fingerprint"):
            self.approve(revision, fingerprint="0" * 64)
        revision.refresh_from_db()
        self.assertEqual(revision.status, ExamGenerationRevision.Status.GENERATED)

        question = Question.objects.filter(contribution__cycle_course=parent).first()
        Question.objects.filter(pk=question.pk).update(
            question_text="Changed after generation but before approval"
        )
        with self.assertRaisesRegex(ApprovalConflict, "drifted"):
            self.approve(revision)
        self.assertFalse(AuditLog.objects.filter(action=self.APPROVAL_ACTION).exists())

        Question.objects.filter(pk=question.pk).update(question_text=question.question_text)
        refreshed_problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=parent
        )
        self.assertTrue(readiness["ready"])
        second = self.generate_with_proved_selection(
            parent=parent,
            problem=refreshed_problem,
            token="n" * 40,
            expected_revision=1,
            regeneration=True,
            reason="Reviewer requested a corrected immutable final arrangement.",
        ).revision
        with self.assertRaisesRegex(ApprovalConflict, "no longer current"):
            self.approve(revision)
        self.assertEqual(second.status, ExamGenerationRevision.Status.GENERATED)

    def test_cycle_draft_and_closed_block_new_approval_open_allows_then_retry_survives_close(self):
        parent, _problem, revision = self.generated_course()
        parent.cycle.status = parent.cycle.Status.DRAFT
        parent.cycle.save(update_fields=["status", "updated_at"])
        with self.assertRaisesRegex(ApprovalConflict, "cycle is not open"):
            self.approve(revision)
        parent.cycle.status = parent.cycle.Status.CLOSED
        parent.cycle.save(update_fields=["status", "updated_at"])
        with self.assertRaisesRegex(ApprovalConflict, "cycle is not open"):
            self.approve(revision)
        parent.cycle.status = parent.cycle.Status.OPEN
        parent.cycle.save(update_fields=["status", "updated_at"])
        self.approve(revision)
        parent.cycle.status = parent.cycle.Status.CLOSED
        parent.cycle.save(update_fields=["status", "updated_at"])
        self.assertTrue(self.approve(revision).reused)

    def test_authorization_feature_scope_assignment_and_direct_deny_are_revalidated(self):
        parent, _problem, revision = self.generated_course()
        with self.assertRaises(PermissionDenied):
            self.approve(revision, actor=self.configurer)
        with self.assertRaises(PermissionDenied):
            self.approve(revision, actor=self.admin)
        with self.assertRaises(Http404):
            ExamApprovalLockService.approve_and_lock(
                revision_id=revision.id,
                tenant_id=self.other_tenant.id,
                actor=self.reviewer,
                expected_revision_number=revision.revision_number,
                expected_source_input_fingerprint=revision.source_input_fingerprint,
            )

        wrong_scope = self.make_user(
            "wrong-scope-reviewer",
            self.other_department,
            ("admin_portal.access", "departmental_exams.review_generate"),
        )
        parent.reviewer = wrong_scope
        parent.save(update_fields=["reviewer", "updated_at"])
        with self.assertRaises(PermissionDenied):
            self.approve(revision, actor=wrong_scope)

        parent.reviewer = self.reviewer
        parent.save(update_fields=["reviewer", "updated_at"])
        UserPermission.objects.create(
            user=self.reviewer,
            permission=Permission.objects.get(
                code="departmental_exams.review_generate"
            ),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.department.campus,
        )
        with self.assertRaises(PermissionDenied):
            self.approve(revision)
        UserPermission.objects.filter(user=self.reviewer).delete()

        self.reviewer.is_active = False
        self.reviewer.save(update_fields=["is_active"])
        with self.assertRaises(PermissionDenied):
            self.approve(revision)
        self.reviewer.is_active = True
        self.reviewer.save(update_fields=["is_active"])

        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        with self.assertRaises(PermissionDenied):
            self.approve(revision)

    def test_lock_blocks_generation_before_token_reuse_and_all_stage6_mutation_services(self):
        parent, problem, revision = self.generated_course()
        self.approve(revision)
        with self.assertRaisesRegex(GenerationConflict, "locked"):
            ExamGenerationService.generate(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                actor=self.reviewer,
                expected_current_revision=0,
                expected_input_fingerprint=problem.input_fingerprint,
                request_token="t" * 40,
            )
        with self.assertRaisesRegex(Stage6Conflict, "locked"):
            BlueprintMutationService.save_structure(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                actor=self.configurer,
                expected_revision=parent.exam_blueprint.revision,
                mode=parent.exam_blueprint.mode,
                sections=[],
            )
        question_ids = list(
            Question.objects.filter(contribution__cycle_course=parent)
            .order_by("id")
            .values_list("id", flat=True)[:2]
        )
        with self.assertRaisesRegex(Stage6Conflict, "locked"):
            QuestionPlacementService.place(
                question_id=question_ids[0],
                section_id=999999,
                tenant_id=self.tenant.id,
                actor=self.reviewer,
                expected_placement_revision=0,
            )
        with self.assertRaisesRegex(Stage6Conflict, "locked"):
            ScenarioMutationService.save(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                actor=self.reviewer,
                title="Denied",
                stimulus="This scenario must not be created after final lock.",
                question_ids=question_ids,
                expected_revision=0,
            )

    def test_lock_blocks_generation_relevant_stage4_configuration_writer(self):
        parent, _problem, revision = self.generated_course()
        self.approve(revision)
        configuration = parent.configuration
        with self.assertRaisesRegex(Stage6Conflict, "locked"):
            CourseExamConfigurationService.save_course_draft(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                user=self.configurer,
                expected_revision=configuration.revision,
                final_item_count=50,
                questions_required_per_faculty=50,
                final_item_count_mode="OVERRIDE",
                questions_required_per_faculty_mode="OVERRIDE",
                coverage=configuration.coverage,
                additional_instructions=configuration.additional_instructions,
                contribution_deadline=configuration.contribution_deadline,
                contribution_deadline_mode="OVERRIDE",
            )

    def test_existing_scenario_delete_and_member_mutation_are_denied_after_lock(self):
        parent, _problem = self.ready_generation_course()
        question_ids = list(
            Question.objects.filter(contribution__cycle_course=parent)
            .order_by("id")
            .values_list("id", flat=True)[:2]
        )
        scenario, _changed = ScenarioMutationService.save(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.reviewer,
            title="Final scenario",
            stimulus="A coherent final scenario used to verify permanent lock behavior.",
            question_ids=question_ids,
            expected_revision=0,
        )
        problem, readiness = Stage6ReadinessService.build_problem(cycle_course=parent)
        self.assertTrue(readiness["ready"], readiness["blockers"])
        revision = ExamGenerationService.generate(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.reviewer,
            expected_current_revision=0,
            expected_input_fingerprint=problem.input_fingerprint,
            request_token="s" * 40,
        ).revision
        before = self.output_snapshot(revision)
        self.approve(revision)
        with self.assertRaisesRegex(Stage6Conflict, "locked"):
            ScenarioMutationService.delete(
                scenario_id=scenario.id,
                tenant_id=self.tenant.id,
                actor=self.reviewer,
                expected_revision=scenario.revision,
            )
        with self.assertRaisesRegex(Stage6Conflict, "locked"):
            ScenarioMutationService.save(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                actor=self.reviewer,
                title="Changed scenario",
                stimulus="This member order and scenario text must not be accepted.",
                question_ids=list(reversed(question_ids)),
                scenario_id=scenario.id,
                expected_revision=scenario.revision,
            )
        self.assertEqual(self.output_snapshot(revision), before)

    def test_reviewer_reassignment_does_not_rewrite_lock_history_or_snapshots(self):
        parent, _problem, revision = self.generated_course()
        before = self.output_snapshot(revision)
        self.approve(revision)
        revision.refresh_from_db()
        locked_at = revision.locked_at
        replacement = self.make_user(
            "replacement-reviewer",
            self.department,
            ("admin_portal.access", "departmental_exams.review_generate"),
        )
        parent.reviewer = replacement
        parent.save(update_fields=["reviewer", "updated_at"])
        revision.refresh_from_db()
        self.assertEqual(revision.locked_by, self.reviewer)
        self.assertEqual(revision.locked_at, locked_at)
        self.assertEqual(self.output_snapshot(revision), before)
        with self.assertRaises(PermissionDenied):
            self.approve(revision)
        self.assertTrue(self.approve(revision, actor=replacement).reused)


class Stage6CIntegrityTests(Stage6CFixtureMixin, Stage4TestCase):
    def test_missing_set_wrong_position_and_corrupt_quota_each_fail_closed(self):
        _parent, _problem, revision = self.generated_course()
        set_b = GeneratedExamSet.objects.get(
            generation_revision=revision, set_code="B"
        )
        GeneratedExamItem.objects.filter(generated_set=set_b).delete()
        GeneratedExamSet.objects.filter(pk=set_b.pk).delete()
        with self.assertRaisesRegex(ApprovalConflict, "Set A and Set B"):
            self.approve(revision)

    def test_wrong_position_fails_without_partial_lock(self):
        _parent, _problem, revision = self.generated_course()
        item = GeneratedExamItem.objects.filter(
            generated_set__generation_revision=revision,
            generated_set__set_code="A",
            position=50,
        ).get()
        GeneratedExamItem.objects.filter(pk=item.pk).update(position=51)
        with self.assertRaisesRegex(ApprovalConflict, "ordering"):
            self.approve(revision)
        revision.refresh_from_db()
        self.assertEqual(revision.status, ExamGenerationRevision.Status.GENERATED)
        self.assertIsNone(revision.locked_at)

    def test_corrupt_quota_overlap_and_snapshot_digest_fail(self):
        _parent, _problem, revision = self.generated_course()
        generated_set = GeneratedExamSet.objects.get(
            generation_revision=revision, set_code="A"
        )
        GeneratedExamSet.objects.filter(pk=generated_set.pk).update(
            campus_quotas_snapshot={"CUBAO": 50}
        )
        with self.assertRaisesRegex(ApprovalConflict, "quota"):
            self.approve(revision)

        GeneratedExamSet.objects.filter(pk=generated_set.pk).update(
            campus_quotas_snapshot=generated_set.campus_quotas_snapshot
        )
        ExamGenerationRevision.objects.filter(pk=revision.pk).update(minimum_overlap=1)
        with self.assertRaisesRegex(ApprovalConflict, "overlap"):
            self.approve(revision)
        ExamGenerationRevision.objects.filter(pk=revision.pk).update(minimum_overlap=0)

        item = GeneratedExamItem.objects.filter(
            generated_set__generation_revision=revision
        ).first()
        GeneratedExamItem.objects.filter(pk=item.pk).update(
            correct_answer_snapshot="B" if item.correct_answer_snapshot != "B" else "A"
        )
        with self.assertRaisesRegex(ApprovalConflict, "corrupt"):
            self.approve(revision)

    def test_persisted_item_divergence_from_authoritative_problem_fails_closed(self):
        _parent, _problem, revision = self.generated_course()
        item = GeneratedExamItem.objects.filter(
            generated_set__generation_revision=revision
        ).first()
        original = {
            field: getattr(item, field)
            for field in (
                "source_question_revision",
                "source_question_digest",
                "source_contributor_id",
                "source_contributor_id_snapshot",
                "source_contributor_name_snapshot",
                "source_campus_id",
                "campus_code_snapshot",
                "campus_name_snapshot",
                "difficulty_snapshot",
                "section_title_snapshot",
                "section_instructions_snapshot",
                "question_text_snapshot",
                "choices_snapshot",
                "source_scenario_id",
                "scenario_id_snapshot",
                "scenario_revision_snapshot",
                "scenario_title_snapshot",
                "scenario_stimulus_snapshot",
                "scenario_member_position_snapshot",
            )
        }
        other_cycle = self.make_cycle(scope_suffix="S6C-SCENARIO-EVIDENCE")
        other_parent = self.make_course(
            cycle=other_cycle,
            code="S6C-SCENARIO-EVIDENCE",
        )
        other_blueprint = ExamBlueprint.objects.create(
            cycle_course=other_parent,
            mode=ExamBlueprint.Mode.NO_SECTIONS,
            created_by=self.configurer,
            updated_by=self.configurer,
        )
        foreign_scenario = ExamScenario.objects.create(
            blueprint=other_blueprint,
            title="Foreign scenario",
            stimulus="Foreign authoritative evidence must never match this exam.",
            created_by=self.reviewer,
            updated_by=self.reviewer,
        )
        cases = (
            (
                "question text",
                {"question_text_snapshot": f"{item.question_text_snapshot} altered"},
                True,
            ),
            (
                "choice",
                {"choices_snapshot": [f"{item.choices_snapshot[0]} altered", *item.choices_snapshot[1:]]},
                True,
            ),
            (
                "source revision and digest",
                {"source_question_revision": item.source_question_revision + 1},
                True,
            ),
            (
                "contributor identity",
                {
                    "source_contributor_id": self.configurer.id,
                    "source_contributor_id_snapshot": self.configurer.id,
                    "source_contributor_name_snapshot": self.configurer.full_name,
                },
                False,
            ),
            (
                "campus identity",
                {
                    "source_campus_id": self.other_campus.id,
                    "campus_code_snapshot": self.other_campus.code,
                    "campus_name_snapshot": self.other_campus.name,
                },
                False,
            ),
            (
                "difficulty",
                {
                    "difficulty_snapshot": (
                        Question.Difficulty.MODERATE
                        if item.difficulty_snapshot != Question.Difficulty.MODERATE
                        else Question.Difficulty.EASY
                    )
                },
                True,
            ),
            (
                "section classification",
                {
                    "section_title_snapshot": "Altered authoritative section",
                    "section_instructions_snapshot": "Altered instructions",
                },
                False,
            ),
            (
                "scenario membership and order",
                {
                    "source_scenario_id": foreign_scenario.id,
                    "scenario_id_snapshot": foreign_scenario.id,
                    "scenario_revision_snapshot": foreign_scenario.revision,
                    "scenario_title_snapshot": foreign_scenario.title,
                    "scenario_stimulus_snapshot": foreign_scenario.stimulus,
                    "scenario_member_position_snapshot": 2,
                },
                False,
            ),
        )
        for label, updates, recompute_digest in cases:
            with self.subTest(field=label):
                GeneratedExamItem.objects.filter(pk=item.pk).update(**updates)
                item.refresh_from_db()
                if recompute_digest:
                    GeneratedExamItem.objects.filter(pk=item.pk).update(
                        source_question_digest=self.snapshot_digest(item)
                    )
                with self.assertRaisesRegex(
                    ApprovalConflict, "authoritative generation evidence"
                ):
                    self.approve(revision)
                self.assert_revision_remains_unlocked(revision)
                GeneratedExamItem.objects.filter(pk=item.pk).update(**original)
                item.refresh_from_db()

    def test_duplicate_source_incomplete_snapshot_and_corrupt_scenario_evidence_are_detected(self):
        parent, problem, revision = self.generated_course()
        sets = list(
            GeneratedExamSet.objects.filter(generation_revision=revision).order_by(
                "set_code"
            )
        )
        items = list(
            GeneratedExamItem.objects.filter(
                generated_set__generation_revision=revision
            ).order_by("generated_set__set_code", "position")
        )
        items[1].source_question_id = items[0].source_question_id
        with self.assertRaisesRegex(ApprovalConflict, "duplicate"):
            GeneratedExamIntegrityService.verify(
                revision=revision,
                generated_sets=sets,
                generated_items=items,
                problem=problem,
            )

        items[1].source_question_id = Question.objects.filter(
            generated_exam_items__id=items[1].id
        ).values_list("id", flat=True).get()
        items[0].question_text_snapshot = ""
        with self.assertRaisesRegex(ApprovalConflict, "incomplete"):
            GeneratedExamIntegrityService.verify(
                revision=revision,
                generated_sets=sets,
                generated_items=items,
                problem=problem,
            )

        items[0].refresh_from_db()
        items[0].source_scenario_id = 999
        items[0].scenario_id_snapshot = 999
        items[0].scenario_revision_snapshot = 1
        items[0].scenario_member_position_snapshot = 2
        items[0].scenario_stimulus_snapshot = "Corrupt isolated member"
        with self.assertRaisesRegex(ApprovalConflict, "scenario"):
            GeneratedExamIntegrityService.verify(
                revision=revision,
                generated_sets=sets,
                generated_items=items,
                problem=problem,
            )


class Stage6CViewTests(Stage6CFixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_checklist_is_required_and_successful_post_redirects(self):
        _parent, _problem, revision = self.generated_course()
        self.client.force_login(self.reviewer)
        url = reverse("departmental_exams:approve_and_lock", args=[revision.id])
        acknowledgements = (
            "set_a_reviewed",
            "set_b_reviewed",
            "answer_keys_reviewed",
            "sections_scenarios_reviewed",
            "permanent_lock_acknowledged",
        )
        with patch(
            "apps.departmental_exams.stage6_views.ExamApprovalLockService.approve_and_lock",
            wraps=ExamApprovalLockService.approve_and_lock,
        ) as approval:
            for missing in acknowledgements:
                with self.subTest(missing=missing):
                    invalid = self.approval_post_data(revision)
                    invalid.pop(missing)
                    self.assertEqual(self.client.post(url, invalid).status_code, 400)
                    self.assert_revision_remains_unlocked(revision)
            self.assertEqual(approval.call_count, 0)

            response = self.client.post(url, self.approval_post_data(revision))
            self.assertEqual(approval.call_count, 1)
        self.assertRedirects(
            response,
            reverse(
                "departmental_exams:generated_revision_detail", args=[revision.id]
            ),
        )
        revision.refresh_from_db()
        self.assertEqual(revision.status, ExamGenerationRevision.Status.LOCKED)

    def test_recomputed_corrupt_correct_answer_cannot_be_approved_or_locked(self):
        _parent, problem, revision = self.generated_course()
        item = GeneratedExamItem.objects.filter(
            generated_set__generation_revision=revision
        ).first()
        source_answer = problem.questions[item.source_question_id].correct_answer
        corrupt_answer = "B" if source_answer != "B" else "A"
        self.assertNotEqual(source_answer, corrupt_answer)
        GeneratedExamItem.objects.filter(pk=item.pk).update(
            correct_answer_snapshot=corrupt_answer
        )
        item.refresh_from_db()
        recomputed_digest = self.snapshot_digest(item)
        self.assertNotEqual(recomputed_digest, item.source_question_digest)
        GeneratedExamItem.objects.filter(pk=item.pk).update(
            source_question_digest=recomputed_digest
        )

        self.client.force_login(self.reviewer)
        response = self.client.post(
            reverse("departmental_exams:approve_and_lock", args=[revision.id]),
            self.approval_post_data(revision),
        )

        self.assertEqual(response.status_code, 409)
        self.assertContains(
            response, "authoritative generation evidence", status_code=409
        )
        self.assert_revision_remains_unlocked(revision)
        item.refresh_from_db()
        self.assertEqual(item.correct_answer_snapshot, corrupt_answer)
        self.assertEqual(item.source_question_digest, recomputed_digest)

    def test_final_review_and_locked_ui_have_confidential_history_warning_and_busy_state(self):
        parent, problem, first = self.generated_course()
        second = self.generate_with_proved_selection(
            parent=parent,
            problem=problem,
            token="u" * 40,
            expected_revision=1,
            regeneration=True,
            reason="Reviewer-only reason for a new final arrangement.",
        ).revision
        self.client.force_login(self.reviewer)
        generated = self.client.get(
            reverse("departmental_exams:generated_revision_detail", args=[second.id])
        )
        self.assertContains(generated, "Approve &amp; Lock")
        self.assertContains(generated, "Approval and final lock are one atomic action.")
        self.assertContains(generated, "data-approve-lock-form")
        self.assertContains(
            generated, "Approving and locking the final examination..."
        )
        self.assertContains(generated, "Reviewer-only reason")
        self.assertNotContains(generated, "Contributors represented")
        self.assertContains(generated, "Regenerate")
        markup = generated.content.decode()
        for name in (
            "set_a_reviewed",
            "set_b_reviewed",
            "answer_keys_reviewed",
            "sections_scenarios_reviewed",
            "permanent_lock_acknowledged",
            "expected_revision_number",
            "expected_source_input_fingerprint",
        ):
            control = re.search(
                rf'<input\b[^>]*\bname="{re.escape(name)}"[^>]*>', markup
            )
            self.assertIsNotNone(control, name)
            self.assertNotIn("disabled", control.group(0), name)
        self.assertIn("submitButton.disabled = true", markup)
        self.assertNotIn("control.disabled = true", markup)
        self.assertIn("event.stopImmediatePropagation()", markup)
        self.assertIn("control.setAttribute('tabindex', '-1')", markup)

        self.approve(second)
        locked = self.client.get(
            reverse("departmental_exams:generated_revision_detail", args=[second.id])
        )
        self.assertContains(locked, "LOCKED")
        self.assertContains(locked, "permanent final examination")
        self.assertNotContains(locked, "data-approve-lock-form")
        self.assertNotContains(locked, ">Regenerate<", html=False)
        self.assertNotContains(locked, "Generate Set A and Set B")
        workspace = self.client.get(
            reverse("departmental_exams:generation_workspace", args=[parent.id])
        )
        self.assertContains(workspace, "Final examination locked")
        self.assertNotContains(workspace, 'class="js-generation-form"')
        self.client.force_login(self.configurer)
        blueprint = self.client.get(
            reverse("departmental_exams:blueprint_configuration", args=[parent.id])
        )
        self.assertContains(blueprint, "Blueprint structure is read-only")
        self.assertNotContains(blueprint, "Save blueprint structure")
        self.client.force_login(self.reviewer)
        review = self.client.get(
            reverse("departmental_exams:blueprint_review", args=[parent.id])
        )
        self.assertContains(review, "Confidential blueprint inputs are frozen")
        self.assertNotContains(review, "Save placement")

    def test_direct_generate_and_regenerate_posts_are_409_after_lock(self):
        parent, problem, revision = self.generated_course()
        self.approve(revision)
        self.client.force_login(self.reviewer)
        generation_data = {
            "expected_current_revision": 1,
            "input_fingerprint": problem.input_fingerprint,
            "request_token": "t" * 40,
        }
        self.assertEqual(
            self.client.post(
                reverse("departmental_exams:generate_exam", args=[parent.id]),
                generation_data,
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.post(
                reverse("departmental_exams:regenerate_exam", args=[parent.id]),
                {
                    **generation_data,
                    "request_token": "r" * 40,
                    "reason": "A stale tab tries to regenerate after permanent lock.",
                },
            ).status_code,
            409,
        )

    def test_configurator_cannot_view_locked_content_and_assigned_list_uses_final_action(self):
        _parent, _problem, revision = self.generated_course()
        self.approve(revision)
        detail_url = reverse(
            "departmental_exams:generated_revision_detail", args=[revision.id]
        )
        self.client.force_login(self.configurer)
        self.assertEqual(self.client.get(detail_url).status_code, 403)
        self.client.force_login(self.reviewer)
        listing = self.client.get(
            reverse("departmental_exams:assigned_course_examinations")
        )
        self.assertContains(listing, "Review Final Locked Exam")
        self.assertNotContains(listing, "Generate Sets")
