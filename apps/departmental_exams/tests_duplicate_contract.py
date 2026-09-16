import csv
import io
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import transaction, IntegrityError
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from .duplicate_contract import (VERSION, bundle_identity, narrative_identity, standalone_identity,
                                 question_identity, reconcile, pool_claims)
from .contribution_services import QuestionMutationService, ContributionRosterService
from .question_content import RICH_HTML_V1, plain_text_to_rich_html
from .csv_import import CSV_HEADERS, QuestionCSVImportService, QuestionImportCleanupService
from .models import (FacultyContribution, Question, QuestionIdentityReservation, QuestionImportBatch,
                     ExaminationCycle, ExamCourseEquivalencyGroup, ExamCourseEquivalencyMembership)
from .stage4_test_support import Stage4TestCase, Stage4TransactionTestCase
from .tests_stage5_contributions import Stage5FixtureMixin
from .tests_faculty_cases import FacultyCaseFixtureMixin
from .faculty_case_services import FacultyCaseMutationService
from .tests_stage6_blueprint_readiness import Stage6BlueprintFixtureMixin


class IdentityTests(SimpleTestCase):
    def test_numeric_ordinals_keep_reviewer_color_choices_ordered(self):
        for ordinal in ("1st and 3rd", "2nd/4th", "11th and 21st", "1ST & 3RD",
                        "(1st), (3rd)", "1 st and 3 rd", "1-st and 3-rd", "1.st and 3.rd",
                        "1ˢᵗ and 3ʳᵈ", "2ⁿᵈ and 4ᵗʰ", "1º and 3º", "1ª and 3ª"):
            with self.subTest(ordinal=ordinal):
                original = {**Stage5FixtureMixin.payload("Select colors"), "choice_a": "Red",
                            "choice_b": "Blue", "choice_c": "Green", "choice_d": ordinal}
                swapped = {**original, "choice_b": "Green", "choice_c": "Blue"}
                self.assertNotEqual(standalone_identity(original), standalone_identity(swapped))
                self.assertEqual(standalone_identity(original), standalone_identity(
                    {**original, "correct_answer": "B", "difficulty": "DIFFICULT"}))
        ordinary = {**original, "choice_d": "Yellow"}
        self.assertEqual(standalone_identity(ordinary), standalone_identity(
            {**ordinary, "choice_b": "Green", "choice_c": "Blue"}))
        numbers = Stage5FixtureMixin.payload("Compute the value")
        self.assertEqual(standalone_identity(numbers), standalone_identity(
            {**numbers, "choice_b": numbers["choice_c"], "choice_c": numbers["choice_b"]}))

    def test_math_and_label_dependent_choices_preserve_meaning(self):
        original = Stage5FixtureMixin.payload("Evaluate x²")
        self.assertNotEqual(standalone_identity(original), standalone_identity({**original, "question_text": "Evaluate x2"}))
        self.assertNotEqual(standalone_identity(original), standalone_identity({**original, "choice_a": "x²"}))
        a = {**original, "choice_a": "x²"}
        self.assertNotEqual(standalone_identity(a), standalone_identity({**a, "choice_a": "x2"}))
        for reference in ("Both A and B", "All of the above", "Neither (a) nor (b)", "First and third", "Options 1 and 2"):
            a = {**original, "choice_d": reference}
            b = {**a, "choice_a": a["choice_b"], "choice_b": a["choice_a"]}
            self.assertNotEqual(standalone_identity(a), standalone_identity(b))
            self.assertEqual(standalone_identity(a), standalone_identity({**a, "correct_answer": "B", "difficulty": "DIFFICULT"}))

    def test_choices_are_multiset_and_metadata_is_excluded(self):
        a = Stage5FixtureMixin.payload()
        b = {**a, "choice_a": "4", "choice_d": "1", "difficulty": "DIFFICULT", "correct_answer": "A"}
        self.assertEqual(standalone_identity(a), standalone_identity(b))
        b["choice_a"] = "1"
        self.assertNotEqual(standalone_identity(a), standalone_identity(b))

    def test_formatting_does_not_change_narrative(self):
        self.assertEqual(narrative_identity("<p>Total <strong>₱500</strong></p>"),
                         narrative_identity('<h3 class="tmp-align-right">Total ₱500</h3>'))
        self.assertEqual(narrative_identity("<p>A</p><p>B</p>"), narrative_identity("<p>A<br>B</p>"))

    def test_table_shape_spans_values_symbols_and_order_are_preserved(self):
        samples = ["<table><tbody><tr><td>A</td><td>B</td></tr></tbody></table>",
                   "<table><tbody><tr><td>A B</td></tr></tbody></table>",
                   '<table><tbody><tr><td colspan="2">A B</td></tr></tbody></table>',
                   "<table><tbody><tr><td>B</td><td>A</td></tr></tbody></table>",
                   "<table><tbody><tr><td>A</td></tr><tr><td>B</td></tr></tbody></table>"]
        self.assertEqual(len({narrative_identity(s) for s in samples}), len(samples))
        self.assertNotEqual(narrative_identity("<p>x<sup>2</sup></p>"), narrative_identity("<p>x2</p>"))
        self.assertNotEqual(narrative_identity("<p>−5</p>"), narrative_identity("<p>5</p>"))


class DuplicateWorkflowTests(Stage5FixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.parent, self.configuration = self.make_stage5_course()
        self.faculty = self.make_faculty("duplicate-owner")
        self.other = self.make_faculty("duplicate-other")
        self.make_assignment(self.parent, self.faculty)
        self.other_assignment = self.make_assignment(self.parent, self.other)
        self.initialize(self.parent)
        self.contribution = FacultyContribution.objects.get(faculty_user=self.faculty)
        self.other_contribution = FacultyContribution.objects.get(faculty_user=self.other)

    def create(self, contribution=None, text="Question one", payload=None):
        contribution = contribution or self.contribution
        contribution.refresh_from_db()
        return QuestionMutationService.create(contribution_id=contribution.id, user=contribution.faculty_user,
            tenant_id=self.tenant.id, campus_id=self.campus.id, expected_contribution_revision=contribution.revision,
            payload=payload or self.payload(text))

    def preview(self, texts):
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(CSV_HEADERS)
        writer.writerows([self.payload(text)[field] for field in CSV_HEADERS] for text in texts)
        self.contribution.refresh_from_db()
        return QuestionCSVImportService.create_preview(contribution_id=self.contribution.id,
            uploaded_file=SimpleUploadedFile("questions.csv", stream.getvalue().encode()), user=self.faculty,
            tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision)

    def process(self, batch, chunk=10):
        return QuestionCSVImportService.process_next_chunk(token=batch.token, expected_file_sha256=batch.file_sha256,
            user=self.faculty, tenant_id=self.tenant.id, campus_id=self.campus.id, chunk_size=chunk)[0]

    def delete(self, question):
        contribution = question.contribution
        contribution.refresh_from_db()
        QuestionMutationService.delete(contribution_id=contribution.id, question_id=question.id,
            user=contribution.faculty_user, tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=contribution.revision, expected_question_revision=question.revision)

    def test_cross_faculty_reordered_choices_rejected_and_delete_releases(self):
        first = self.create(self.other_contribution)
        with self.assertRaises(ValidationError):
            self.create(payload={**self.payload("Question one"), "choice_a": "4", "choice_d": "1"})
        self.assertEqual(Question.objects.count(), 1)
        self.delete(first)
        self.create()
        self.assertEqual(QuestionIdentityReservation.objects.count(), 1)

    def test_editor_projection_noop_preserves_plain_storage_and_revisions(self):
        question = self.create(text="plain\nquestion")
        before_contribution = question.contribution.revision
        rich_payload = self.payload("plain\nquestion")
        rich_payload["content_format"] = RICH_HTML_V1
        for field in ("question_text", "choice_a", "choice_b", "choice_c", "choice_d"):
            rich_payload[field] = plain_text_to_rich_html(rich_payload[field])
        unchanged, changed = QuestionMutationService.update(
            contribution_id=self.contribution.id, question_id=question.id,
            user=self.faculty, tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=before_contribution,
            expected_question_revision=question.revision, payload=rich_payload,
        )
        question.refresh_from_db()
        self.contribution.refresh_from_db()
        self.assertFalse(changed)
        self.assertEqual(unchanged.id, question.id)
        self.assertEqual(question.content_format, "PLAIN_TEXT")
        self.assertEqual(question.question_text, "plain\nquestion")
        self.assertEqual(question.revision, 1)
        self.assertEqual(self.contribution.revision, before_contribution)

    def test_v3_reservations_transition_atomically_to_v4(self):
        first = self.create(text="first")
        QuestionIdentityReservation.objects.filter(question=first).update(version="course-question-v3")
        self.create(text="second")
        reservations = list(QuestionIdentityReservation.objects.order_by("question_id"))
        self.assertEqual([row.version for row in reservations], [VERSION, VERSION])
        self.assertEqual([row.question_id for row in reservations], [first.id, reservations[1].question_id])

    def test_v3_import_plan_is_owner_cleaned_not_relabelled(self):
        batch = self.preview(["pending"])
        from .duplicate_contract import plan_import
        plan = plan_import(self.contribution, list(batch.rows.order_by("row_number")))
        row_number = next(iter(plan))
        decision = dict(plan[row_number])
        decision["identity_version"] = "course-question-v3"
        plan[row_number] = decision
        batch.duplicate_plan = plan
        batch.save(update_fields=["duplicate_plan", "updated_at"])
        with self.assertRaises(ValidationError):
            self.process(batch)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QuestionImportBatch.Status.FAILED)
        self.assertEqual(batch.failure_code, "IMPORT_PLAN_VERSION")
        self.assertFalse(Question.objects.filter(import_batch=batch).exists())
        self.assertFalse(QuestionIdentityReservation.objects.filter(import_batch=batch).exists())

    def test_manual_bound_input_and_confidentiality(self):
        self.create(self.other_contribution)
        self.client.force_login(self.faculty)
        payload = {**self.payload("Question one"), "correct_answer": "D", "difficulty": "EASY", "expected_contribution_revision": self.contribution.revision}
        response = self.client.post(reverse("departmental_exams:question_create", args=[self.contribution.id]), payload)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.context["form"]["question_text"].value(), "Question one")
        self.assertIn("already represented", str(response.context["form"].errors))
        self.assertNotContains(response, self.other.username, status_code=400)
        self.assertEqual(Question.objects.count(), 1)

    def test_equivalent_members_share_primary_reservation(self):
        ExaminationCycle.objects.filter(pk=self.parent.cycle_id).update(processing_mode="AUTOMATIC_GENERATION")
        self.parent.refresh_from_db()
        alias = self.make_course(cycle=self.parent.cycle, code="ALIAS")
        config = self.make_configuration(alias, workflow="OPEN", opened_at=self.configuration.opened_at,
            deadline=self.configuration.contribution_deadline)
        # Explicit fixture mapping; production grouping governance remains untouched.
        from .models import _equivalency_lifecycle_service_scope
        with _equivalency_lifecycle_service_scope():
            group = ExamCourseEquivalencyGroup.objects.create(cycle=self.parent.cycle, name="Equivalent",
                primary_cycle_course=self.parent, created_by=self.admin, updated_by=self.admin)
            for course in (self.parent, alias):
                ExamCourseEquivalencyMembership.objects.create(group=group, cycle_course=course, added_by=self.admin)
        first = self.create()
        moved = self.other_contribution
        FacultyContribution.objects.filter(pk=moved.id).update(cycle_course=alias)
        moved.refresh_from_db()
        Question.objects.create(contribution=moved, position=1, **{**self.payload("Question one"), "correct_answer": "D", "difficulty": "EASY"})
        with transaction.atomic(), self.assertRaises(ValidationError):
            reconcile(alias)
        self.assertEqual(QuestionIdentityReservation.objects.get(question=first).primary_cycle_course_id, self.parent.id)

    def test_cross_cycle_independence(self):
        self.create()
        cycle = self.make_cycle(status="OPEN", scope_suffix="next")
        other_course = self.make_course(cycle=cycle, code="NEXT")
        self.make_configuration(other_course)
        other = FacultyContribution.objects.create(cycle_course=other_course, faculty_user=self.faculty,
            source_campus=self.campus, quota_snapshot=50, configuration_revision_snapshot=1)
        q = Question.objects.create(contribution=other, position=1,
            **{**self.payload("Question one"), "correct_answer": "D", "difficulty": "EASY"})
        with transaction.atomic():
            reconcile(other_course)
        self.assertEqual(QuestionIdentityReservation.objects.count(), 2)

    def test_import_skips_duplicates_capacity_and_retry(self):
        self.create(self.other_contribution, text="shared")
        for i in range(45):
            self.create(text=f"existing {i}")
        batch = self.preview(["shared", "shared", "a", "b", "c", "d", "e"])
        self.assertEqual(batch.status, "READY")
        self.assertEqual(batch.resulting_question_count, 50)
        batch = self.process(batch, 3)
        plan = batch.duplicate_plan.copy()
        self.assertEqual(Question.objects.filter(import_batch=batch).count(), 1)
        with self.assertRaises(ValidationError):
            self.create(self.other_contribution, text="e")
        batch = self.process(batch)
        self.assertEqual(batch.status, "CONFIRMED")
        self.assertEqual((batch.accepted_rows, batch.skipped_rows), (5, 2))
        self.assertEqual(self.contribution.questions.count(), 50)
        self.assertEqual(self.process(batch).duplicate_plan, plan)
        self.assertEqual(self.contribution.questions.count(), 50)
        self.contribution.refresh_from_db()
        submitted, changed = QuestionMutationService.submit(contribution_id=self.contribution.id,
            user=self.faculty, tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision)
        self.assertTrue(changed)
        self.assertEqual(submitted.status, "SUBMITTED")

    def test_all_duplicate_import_completes_without_question_or_claim(self):
        self.create(self.other_contribution, text="shared")
        batch = self.process(self.preview(["shared", "shared"]), 1)
        batch = self.process(batch)
        self.assertEqual((batch.status, batch.accepted_rows, batch.skipped_rows), ("CONFIRMED", 0, 2))
        self.assertEqual(self.contribution.questions.count(), 0)
        self.assertEqual(QuestionIdentityReservation.objects.count(), 1)

    def test_overflow_and_malformed_rows_still_block_batch(self):
        for i in range(49):
            self.create(text=f"existing {i}")
        batch = self.preview(["a", "b"])
        self.assertEqual(batch.status, "INVALID")
        with self.assertRaises(ValidationError):
            self.process(batch)
        self.assertEqual(self.contribution.questions.count(), 49)

    def test_terminal_failure_releases_only_unpublished_claims(self):
        accepted = self.create(self.other_contribution, text="published")
        batch = self.process(self.preview(["a", "b", "c"]), 1)
        QuestionCSVImportService._record_terminal_failure(token=batch.token, user=self.faculty,
            tenant_id=self.tenant.id, failure_code="INVALID_IMPORT_STATE")
        self.assertEqual(self.contribution.questions.count(), 0)
        self.assertEqual(list(QuestionIdentityReservation.objects.values_list("question_id", flat=True)), [accepted.id])
        self.create(self.other_contribution, text="b")

    def test_failed_chunk_resume_keeps_decisions(self):
        self.create(self.other_contribution, text="shared")
        batch = self.process(self.preview(["shared", "a", "b"]), 1)
        plan = batch.duplicate_plan.copy()
        with patch("apps.departmental_exams.csv_import.QuestionPayloadService.validate", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                self.process(batch)
        batch.refresh_from_db()
        self.assertEqual(batch.status, "PAUSED")
        self.assertEqual(batch.duplicate_plan, plan)
        self.assertEqual(self.process(batch).status, "CONFIRMED")
        self.assertEqual(self.contribution.questions.count(), 2)

    def test_blocked_sufficient_pool_reactivation_rolls_back_on_collision(self):
        ExaminationCycle.objects.filter(pk=self.parent.cycle_id).update(
            processing_mode="AUTOMATIC_GENERATION", automatic_contributor_completion_policy="SUFFICIENT_POOL")
        old = self.create(self.other_contribution, text="shared")
        FacultyContribution.objects.filter(pk=self.other_contribution.id).update(roster_status="BLOCKED", roster_blocked_at=timezone.now())
        self.create(text="shared")
        self.assertFalse(QuestionIdentityReservation.objects.filter(question=old).exists())
        with self.assertRaises(ValidationError), transaction.atomic():
            FacultyContribution.objects.filter(pk=self.other_contribution.id).update(roster_status="ACTIVE", roster_blocked_at=None)
            reconcile(self.parent)
        self.other_contribution.refresh_from_db()
        self.assertEqual(self.other_contribution.roster_status, "BLOCKED")
        self.assertTrue(Question.objects.filter(pk=old.id).exists())

    def test_legacy_preflight_reports_ids_without_winner_or_mutation(self):
        self.create()
        Question.objects.create(contribution=self.other_contribution, position=1,
            **{**self.payload("Question one"), "correct_answer": "D", "difficulty": "EASY"})
        before = list(QuestionIdentityReservation.objects.values())
        output = io.StringIO()
        call_command("duplicate_question_preflight", tenant_id=self.tenant.id, cycle_course_id=self.parent.id, stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual(report["collision_count"], 1)
        self.assertNotIn("Question one", output.getvalue())
        self.assertNotIn(self.other.username, output.getvalue())
        self.assertEqual(list(QuestionIdentityReservation.objects.values()), before)
        with self.assertRaises(ValidationError):
            self.create(text="unrelated")
        self.assertEqual(Question.objects.count(), 2)

    def test_unique_constraint_enforces_one_owner(self):
        first = self.create()
        reservation = QuestionIdentityReservation.objects.get(question=first)
        batch = self.preview(["pending"])
        with self.assertRaises(IntegrityError), transaction.atomic():
            QuestionIdentityReservation.objects.create(primary_cycle_course=self.parent, version=VERSION,
                fingerprint=reservation.fingerprint, import_batch=batch, import_row_number=2)

    def test_edit_collision_preserves_saved_question(self):
        self.create(self.other_contribution, text="shared")
        question = self.create(text="original")
        self.contribution.refresh_from_db()
        with self.assertRaises(ValidationError):
            QuestionMutationService.update(contribution_id=self.contribution.id, question_id=question.id,
                user=self.faculty, tenant_id=self.tenant.id, campus_id=self.campus.id,
                expected_contribution_revision=self.contribution.revision, expected_question_revision=question.revision,
                payload=self.payload("shared"))
        question.refresh_from_db()
        self.assertEqual(question.question_text, "original")

    def test_generation_groups_share_choice_multiset_identity(self):
        from .generation_readiness import automatic_logical_question_groups
        first = self.create()
        second = Question.objects.create(contribution=self.other_contribution, position=1,
            **{**self.payload("Question one"), "choice_a": "4", "choice_d": "1", "correct_answer": "A", "difficulty": "EASY"})
        groups = automatic_logical_question_groups([first, second])
        self.assertEqual(len(groups), 1)
        self.assertEqual(next(iter(groups)), QuestionIdentityReservation.objects.get(question=first).fingerprint)

    def test_malformed_row_is_not_accepted_as_duplicate(self):
        self.create(self.other_contribution, text="shared")
        batch = self.preview(["shared", "new"])
        row = batch.rows.order_by("row_number").first()
        row.payload["correct_answer"] = "invalid"
        row.save(update_fields=["payload"])
        with self.assertRaises(ValidationError):
            self.process(batch)
        self.assertFalse(self.contribution.questions.exists())

    def test_published_import_claim_survives_shell_cleanup(self):
        batch = self.process(self.preview(["a"]))
        question = self.contribution.questions.get()
        QuestionImportBatch.objects.filter(pk=batch.pk).update(created_at=timezone.now() - timezone.timedelta(days=400))
        QuestionImportCleanupService.purge()
        self.assertTrue(QuestionIdentityReservation.objects.filter(question=question).exists())
        with self.assertRaises(ValidationError):
            self.create(self.other_contribution, text="a")

    def test_abandoned_import_releases_claims_but_recent_progress_prevents_cleanup(self):
        batch = self.process(self.preview(["a", "b"]), 1)
        before = timezone.now() - timezone.timedelta(hours=24)
        QuestionCSVImportService._record_terminal_failure(token=batch.token, user=self.faculty,
            tenant_id=self.tenant.id, failure_code="AUTHORIZATION_CHANGED", inactive_before=before)
        batch.refresh_from_db()
        self.assertEqual(batch.status, "IMPORTING")
        QuestionImportBatch.objects.filter(pk=batch.pk).update(progress_updated_at=before - timezone.timedelta(minutes=1))
        QuestionImportCleanupService.purge()
        batch.refresh_from_db()
        self.assertEqual(batch.status, "FAILED")
        self.assertFalse(QuestionIdentityReservation.objects.exists())

    def test_roster_reactivation_reacquires_or_rolls_back(self):
        ExaminationCycle.objects.filter(pk=self.parent.cycle_id).update(
            processing_mode="AUTOMATIC_GENERATION", automatic_contributor_completion_policy="SUFFICIENT_POOL")
        old = self.create(self.other_contribution, text="shared")
        self.other_assignment.is_active = False
        self.other_assignment.save(update_fields=["is_active"])
        ContributionRosterService.synchronize(cycle_course_id=self.parent.id, tenant_id=self.tenant.id, actor=self.admin)
        self.assertFalse(QuestionIdentityReservation.objects.filter(question=old).exists())
        competitor = self.create(text="shared")
        self.other_assignment.is_active = True
        self.other_assignment.save(update_fields=["is_active"])
        with self.assertRaises(ValidationError):
            ContributionRosterService.synchronize(cycle_course_id=self.parent.id, tenant_id=self.tenant.id, actor=self.admin)
        self.other_contribution.refresh_from_db()
        self.assertEqual(self.other_contribution.roster_status, "BLOCKED")
        self.delete(competitor)
        ContributionRosterService.synchronize(cycle_course_id=self.parent.id, tenant_id=self.tenant.id, actor=self.admin)
        self.assertTrue(QuestionIdentityReservation.objects.filter(question=old).exists())

    def test_word_correction_confirmation_and_resume_share_decisions(self):
        from apps.core.services.settings import SystemSettingService
        from apps.core.services.features import FeatureSettingsService
        from .docx_import import QuestionDOCXImportService
        from .tests_docx_import import make_docx
        SystemSettingService.set(FeatureSettingsService.DEPARTMENTAL_EXAM_DOCX_IMPORT_ENABLED_KEY,
            True, tenant_id=self.tenant.id, value_type="BOOL", is_active=True)
        self.create(self.other_contribution, text="shared")
        paragraphs = ["1. shared", "A. 1", "B. 2", "C. 3", "D. 4", "Answer: D", "Difficulty: Easy",
                      "2. new", "A. 1", "B. 2", "C. 3", "D. 4", "Answer: D"]
        batch = QuestionDOCXImportService.create_preview(contribution_id=self.contribution.id,
            uploaded_file=make_docx(paragraphs), user=self.faculty, tenant_id=self.tenant.id,
            campus_id=self.campus.id, expected_contribution_revision=self.contribution.revision)
        self.assertEqual(batch.status, "INVALID")
        batch, row = QuestionDOCXImportService.update_staged_row(token=batch.token, row_number=3,
            payload=self.payload("new"), user=self.faculty, tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision)
        self.assertEqual(batch.status, "READY")
        batch = self.process(batch, 1)
        self.assertEqual(batch.committed_rows, 1)
        self.assertEqual(self.contribution.questions.count(), 0)
        batch = self.process(batch)
        self.assertEqual((batch.accepted_rows, batch.skipped_rows), (1, 1))
        self.assertEqual(self.contribution.questions.get().entry_method, "DOCX")


    def test_strict_and_manual_blocked_drafts_keep_claims(self):
        old = self.create(self.other_contribution, text="shared")
        FacultyContribution.objects.filter(pk=self.other_contribution.id).update(
            roster_status="BLOCKED", roster_blocked_at=timezone.now())
        for mode in ("MANUAL_REVIEW", "AUTOMATIC_GENERATION"):
            ExaminationCycle.objects.filter(pk=self.parent.cycle_id).update(
                processing_mode=mode, automatic_contributor_completion_policy="REQUIRE_ALL")
            with self.assertRaises(ValidationError):
                self.create(text="shared")
            self.assertTrue(QuestionIdentityReservation.objects.filter(question=old).exists())

    def test_terminal_cleanup_ignores_unrelated_legacy_collisions(self):
        protected = self.create(self.other_contribution, text="legacy")
        batch = self.process(self.preview(["a", "b", "c"]), 1)
        claim = QuestionIdentityReservation.objects.get(question=protected)
        Question.objects.create(contribution=self.other_contribution, position=2,
            **{**self.payload("legacy"), "correct_answer": "D", "difficulty": "EASY"})
        QuestionCSVImportService._record_terminal_failure(token=batch.token, user=self.faculty,
            tenant_id=self.tenant.id, failure_code="INVALID_IMPORT_STATE")
        batch.refresh_from_db()
        self.assertEqual(batch.status, "FAILED")
        self.assertFalse(Question.objects.filter(import_batch=batch).exists())
        self.assertFalse(batch.rows.exists())
        self.assertFalse(QuestionIdentityReservation.objects.filter(import_batch=batch).exists())
        self.assertTrue(QuestionIdentityReservation.objects.filter(pk=claim.pk, question=protected).exists())
        self.assertEqual(self.other_contribution.questions.count(), 2)

    def test_legacy_conflict_has_distinct_safe_guidance_and_fails_batch(self):
        from .duplicate_contract import LegacyPoolConflict, LEGACY_POOL_MESSAGE
        batch = self.preview(["new"])
        for position in (1, 2):
            Question.objects.create(contribution=self.other_contribution, position=position,
                **{**self.payload("private legacy"), "correct_answer": "D", "difficulty": "EASY"})
        with self.assertRaisesMessage(LegacyPoolConflict, "administrative review"):
            self.process(batch)
        batch.refresh_from_db()
        self.assertEqual((batch.status, batch.failure_message), ("FAILED", LEGACY_POOL_MESSAGE))
        with self.assertRaisesMessage(LegacyPoolConflict, "administrative review"):
            self.create(text="unrelated input")
        self.assertNotIn("private legacy", batch.failure_message)
        self.assertNotIn(self.other.username, batch.failure_message)

    def test_multiple_chunks_transfer_only_chunk_claims(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        protected = self.create(self.other_contribution, text="protected")
        claim_id = QuestionIdentityReservation.objects.get(question=protected).pk
        batch = self.process(self.preview([f"import {i}" for i in range(6)]), 2)
        pending_ids = set(QuestionIdentityReservation.objects.filter(import_batch=batch).values_list("pk", flat=True))
        for _ in range(2):
            with CaptureQueriesContext(connection) as queries, patch(
                "apps.departmental_exams.duplicate_contract.pool_claims", side_effect=AssertionError("chunk must not scan the pool")
            ):
                batch = self.process(batch, 2)
            claim_writes = [q["sql"] for q in queries if "departmental_exams_questionidentityreservation" in q["sql"].lower()
                            and q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
            self.assertEqual(len(claim_writes), 2, claim_writes)
            self.assertTrue(all(sql.lstrip().upper().startswith("UPDATE") for sql in claim_writes))
        self.assertEqual(batch.status, "CONFIRMED")
        self.assertTrue(QuestionIdentityReservation.objects.filter(pk=claim_id, question=protected).exists())
        self.assertEqual(QuestionIdentityReservation.objects.filter(pk__in=pending_ids, question__isnull=False).count(), 4)

    def test_bad_plan_versions_block_initializers_then_owner_cleanup_is_idempotent(self):
        from .duplicate_contract import IncompatibleImportPlan, IMPORT_PLAN_VERSION_MESSAGE
        from django.db.models import Q
        from django.core.management.base import CommandError
        completed = self.process(self.preview(["published protection"]))
        published = Question.objects.get(import_batch=completed)
        protected_claim = QuestionIdentityReservation.objects.get(question=published)
        for variant in ("missing", "incompatible", "mixed", "empty"):
            with self.subTest(variant=variant):
                batch = self.process(self.preview(["published protection", f"first {variant}", f"pending {variant}"]), 2)
                self.assertTrue(all(d["identity_version"] == VERSION for d in batch.duplicate_plan.values()))
                # A separate owner's READY upload exists before legacy corruption.
                self.contribution, self.other_contribution = self.other_contribution, self.contribution
                self.faculty, self.other = self.other, self.faculty
                competitor = self.preview([f"competitor {variant}"])
                self.contribution, self.other_contribution = self.other_contribution, self.contribution
                self.faculty, self.other = self.other, self.faculty
                plan = batch.duplicate_plan
                for i, decision in enumerate(plan.values()):
                    if variant == "missing":
                        decision.pop("identity_version")
                    elif variant == "incompatible" or (variant == "mixed" and i == 0):
                        decision["identity_version"] = "course-question-v2"
                if variant == "empty":
                    plan = {}
                QuestionImportBatch.objects.filter(pk=batch.pk).update(duplicate_plan=plan)
                # Simulate legacy stored reservations, never rebrand their digests.
                QuestionIdentityReservation.objects.filter(Q(import_batch=batch) | Q(question__import_batch=batch)).update(version="course-question-v2")
                before_claims = list(QuestionIdentityReservation.objects.order_by("pk").values())
                before_questions = list(Question.objects.order_by("pk").values())
                before_contributions = list(FacultyContribution.objects.order_by("pk").values())
                with self.assertRaisesMessage(CommandError, "upload the file again"):
                    call_command("duplicate_question_preflight", tenant_id=self.tenant.id,
                        cycle_course_id=self.parent.id, stdout=io.StringIO())
                with self.assertRaises(IncompatibleImportPlan), transaction.atomic():
                    reconcile(self.parent)
                with self.assertRaises(IncompatibleImportPlan):
                    self.create(self.other_contribution, text="new writer input")
                with self.assertRaises(IncompatibleImportPlan):
                    QuestionCSVImportService.process_next_chunk(token=competitor.token,
                        expected_file_sha256=competitor.file_sha256, user=self.other,
                        tenant_id=self.tenant.id, campus_id=self.campus.id)
                competitor.refresh_from_db()
                self.assertEqual((competitor.status, competitor.duplicate_plan), ("READY", {}))
                self.assertEqual(list(QuestionIdentityReservation.objects.order_by("pk").values()), before_claims)
                self.assertEqual(list(Question.objects.order_by("pk").values()), before_questions)
                self.assertEqual(list(FacultyContribution.objects.order_by("pk").values()), before_contributions)
                with self.assertRaisesMessage(IncompatibleImportPlan, "upload the file again"):
                    self.process(batch)
                batch.refresh_from_db()
                self.assertEqual((batch.status, batch.failure_message), ("FAILED", IMPORT_PLAN_VERSION_MESSAGE))
                self.assertFalse(batch.rows.exists())
                self.assertFalse(Question.objects.filter(import_batch=batch).exists())
                self.assertFalse(QuestionIdentityReservation.objects.exclude(version=VERSION).exists())
                failed_state = QuestionImportBatch.objects.get(pk=batch.pk).__dict__.copy()
                with self.assertRaisesMessage(ValidationError, "upload the file again"):
                    self.process(batch)
                self.assertFalse(QuestionCSVImportService._record_terminal_failure(token=batch.token,
                    user=self.faculty, tenant_id=self.tenant.id, failure_code="IMPORT_PLAN_VERSION"))
                current = QuestionImportBatch.objects.get(pk=batch.pk).__dict__.copy()
                for snapshot in (failed_state, current):
                    snapshot.pop("_state")
                self.assertEqual(current, failed_state)
                self.assertTrue(QuestionIdentityReservation.objects.filter(pk=protected_claim.pk, question=published).exists())
                # Published batches bypass pending-plan compatibility checks.
                QuestionImportBatch.objects.filter(pk=completed.pk).update(duplicate_plan={})
                self.assertEqual(self.process(completed).status, "CONFIRMED")

    def test_matching_version_initializer_preserves_digests_then_resume_transfers_once(self):
        from django.db.models import Q
        batch = self.process(self.preview(["one", "two", "three"]), 1)
        batch.refresh_from_db()
        self.assertTrue(all(d["identity_version"] == VERSION for d in batch.duplicate_plan.values()))
        # Exercise legitimate initialization of absent derived claims.
        QuestionIdentityReservation.objects.filter(Q(import_batch=batch) | Q(question__import_batch=batch)).delete()
        with transaction.atomic():
            reconcile(self.parent)
        pending = list(QuestionIdentityReservation.objects.filter(import_batch=batch).order_by("import_row_number"))
        self.assertEqual(len(pending), 2)
        for claim in pending:
            decision = batch.duplicate_plan[str(claim.import_row_number)]
            self.assertEqual((claim.version, claim.fingerprint), (decision["identity_version"], decision["identity"]))
        self.create(self.other_contribution, text="independent writer")
        plan = batch.duplicate_plan.copy()
        batch = self.process(batch)
        self.assertEqual((batch.status, batch.duplicate_plan), ("CONFIRMED", plan))
        self.assertTrue(all(QuestionIdentityReservation.objects.filter(pk=c.pk, question__isnull=False).exists() for c in pending))
        claim_state = list(QuestionIdentityReservation.objects.order_by("pk").values())
        self.process(batch)
        self.assertEqual(list(QuestionIdentityReservation.objects.order_by("pk").values()), claim_state)

    def test_corrected_identity_used_by_manual_import_and_generation(self):
        from .generation_readiness import automatic_logical_question_groups
        first = self.create(text="Evaluate x²")
        batch = self.process(self.preview(["Evaluate x2", "Evaluate x²"]))
        self.assertEqual((batch.accepted_rows, batch.skipped_rows), (1, 1))
        questions = list(self.contribution.questions.all())
        self.assertEqual(len(automatic_logical_question_groups(questions)), 2)
        self.assertEqual(set(automatic_logical_question_groups(questions)),
            set(QuestionIdentityReservation.objects.filter(question__in=questions).values_list("fingerprint", flat=True)))

    def test_preflight_bound_includes_pending_candidates(self):
        self.process(self.preview(["a", "b", "c"]), 1)
        with self.assertRaisesMessage(ValidationError, "bounded candidate limit"):
            pool_claims(self.parent, limit=2)


class CaseDuplicateTests(FacultyCaseFixtureMixin, Stage4TestCase):
    def test_distinct_narratives_allow_same_member_and_formatting_edit_keeps_identity(self):
        first = self.save_case(html="<p>Value 100</p>")
        second = self.save_case(html="<p>Value 200</p>")
        a = self.add_question(scenario=first)
        b = self.add_question(scenario=second)
        self.assertNotEqual(question_identity(a), question_identity(b))
        key = QuestionIdentityReservation.objects.get(question=a).fingerprint
        first.refresh_from_db()
        self.contribution.refresh_from_db()
        FacultyCaseMutationService.save(contribution_id=self.contribution.id, user=self.faculty,
            tenant_id=self.tenant.id, campus_id=self.campus.id, expected_contribution_revision=self.contribution.revision,
            title=first.title, raw_content="<p><strong>Value 100</strong></p>", section_id=self.section_a.id,
            scenario_id=first.id, expected_scenario_revision=first.revision)
        self.assertEqual(QuestionIdentityReservation.objects.get(question=a).fingerprint, key)

    def test_narrative_collision_rolls_back_whole_case_and_members(self):
        first = self.save_case(html="<p>Value 100</p>")
        second = self.save_case(html="<p>Value 200</p>")
        self.add_question(scenario=first)
        self.add_question(scenario=second)
        self.add_question(scenario=second, text="Another member")
        second.refresh_from_db()
        self.contribution.refresh_from_db()
        before = list(second.members.order_by("position").values_list("question_id", "position"))
        with self.assertRaises(ValidationError):
            FacultyCaseMutationService.save(contribution_id=self.contribution.id, user=self.faculty,
                tenant_id=self.tenant.id, campus_id=self.campus.id, expected_contribution_revision=self.contribution.revision,
                title=second.title, raw_content="<p>Value 100</p>", section_id=self.section_a.id,
                scenario_id=second.id, expected_scenario_revision=second.revision)
        second.refresh_from_db()
        self.assertEqual(second.stimulus, "<p>Value 200</p>")
        self.assertEqual(list(second.members.order_by("position").values_list("question_id", "position")), before)

    def test_bundle_preserves_member_order(self):
        scenario = self.save_case()
        self.add_question(scenario=scenario, text="first")
        self.add_question(scenario=scenario, text="second")
        members = list(scenario.members.select_related("question").order_by("position"))
        self.assertNotEqual(bundle_identity(scenario, members), bundle_identity(scenario, list(reversed(members))))
        before = QuestionIdentityReservation.objects.get(question=members[0].question).bundle_fingerprint
        scenario.refresh_from_db()
        self.contribution.refresh_from_db()
        FacultyCaseMutationService.reorder_members(contribution_id=self.contribution.id, scenario_id=scenario.id,
            ordered_question_ids=[member.question_id for member in reversed(members)], user=self.faculty,
            tenant_id=self.tenant.id, campus_id=self.campus.id, expected_contribution_revision=self.contribution.revision,
            expected_scenario_revision=scenario.revision)
        self.assertNotEqual(before, QuestionIdentityReservation.objects.get(question=members[0].question).bundle_fingerprint)


class AdminScenarioDuplicateTests(Stage6BlueprintFixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        from apps.rbac.models import Permission
        Permission.objects.get_or_create(code="faculty_portal.access", defaults={
            "module": "faculty_portal", "action": "access", "description": "Faculty Portal", "is_active": True})
        self.parent, _, self.contributions, _ = self.closed_course()
        self.blueprint = self.no_sections_blueprint(self.parent)
        self.questions = list(Question.objects.filter(contribution__cycle_course=self.parent).order_by("id")[:3])

    def save_scenario(self, questions, narrative, scenario=None):
        from .blueprint_services import ScenarioMutationService
        return ScenarioMutationService.save(cycle_course_id=self.parent.id, tenant_id=self.tenant.id,
            actor=self.reviewer, title="Admin scenario", stimulus=narrative,
            question_ids=[q.pk for q in questions], scenario_id=scenario.pk if scenario else None,
            expected_revision=scenario.revision if scenario else 0)[0]

    def prepare_distinct_cases(self):
        first = self.save_scenario(self.questions[:2], "Narrative one")
        duplicates = []
        for index, original in enumerate(self.questions[:2], start=51):
            duplicates.append(Question.objects.create(contribution=original.contribution, position=index,
                **{field: getattr(original, field) for field in ("question_text", "choice_a", "choice_b", "choice_c", "choice_d", "correct_answer", "difficulty")}))
        second = self.save_scenario(duplicates, "Narrative two")
        return first, second, duplicates

    def test_admin_narrative_collision_rolls_back_members_content_and_claims(self):
        first, second, duplicates = self.prepare_distinct_cases()
        before_questions = list(Question.objects.order_by("id").values())
        claims = list(QuestionIdentityReservation.objects.order_by("id").values())
        before_members = list(second.members.order_by("position").values())
        with self.assertRaisesMessage(ValidationError, "already represented"):
            self.save_scenario(duplicates, first.stimulus, second)
        second.refresh_from_db()
        self.assertEqual(second.stimulus, "Narrative two")
        self.assertEqual(list(second.members.order_by("position").values()), before_members)
        self.assertEqual(list(QuestionIdentityReservation.objects.order_by("id").values()), claims)
        self.assertEqual(list(Question.objects.order_by("id").values()), before_questions)

    def test_admin_membership_change_reacquires_removed_singleton_and_preserves_order(self):
        scenario = self.save_scenario(self.questions[:2], "Narrative one")
        extra = Question.objects.create(contribution=self.questions[0].contribution, position=51,
            **{field: getattr(self.questions[0], field) for field in ("question_text", "choice_a", "choice_b", "choice_c", "choice_d", "correct_answer", "difficulty")})
        with self.assertRaisesMessage(ValidationError, "already represented"):
            self.save_scenario([self.questions[2], self.questions[1]], "Narrative one", scenario)
        self.assertEqual(list(scenario.members.order_by("position").values_list("question_id", flat=True)),
                         [self.questions[0].pk, self.questions[1].pk])
        extra.delete()  # Remove only the disposable fixture collision.
        scenario = self.save_scenario([self.questions[2], self.questions[1]], "Narrative one", scenario)
        self.assertEqual(list(scenario.members.order_by("position").values_list("question_id", flat=True)),
                         [self.questions[2].pk, self.questions[1].pk])
        released = QuestionIdentityReservation.objects.get(question=self.questions[0])
        self.assertEqual(released.fingerprint, standalone_identity(self.questions[0]))
        self.assertEqual(released.bundle_fingerprint, "")

    def test_admin_delete_conflict_rolls_back_then_success_releases_only_case_identity(self):
        from .blueprint_services import ScenarioMutationService
        first, second, duplicates = self.prepare_distinct_cases()
        standalone = self.save_scenario([self.questions[2], Question.objects.filter(contribution__cycle_course=self.parent).order_by("id")[3]], "Unrelated")
        protected_id = QuestionIdentityReservation.objects.get(question=self.questions[2]).pk
        extra = Question.objects.create(contribution=duplicates[0].contribution, position=53,
            **{field: getattr(duplicates[0], field) for field in ("question_text", "choice_a", "choice_b", "choice_c", "choice_d", "correct_answer", "difficulty")})
        with self.assertRaisesMessage(ValidationError, "already represented"):
            ScenarioMutationService.delete(scenario_id=second.pk, tenant_id=self.tenant.id,
                actor=self.reviewer, expected_revision=second.revision)
        self.assertEqual(second.members.count(), 2)
        extra.delete()  # Remove only the disposable legacy fixture collision.
        before_questions = list(Question.objects.order_by("id").values())
        ScenarioMutationService.delete(scenario_id=second.pk, tenant_id=self.tenant.id,
            actor=self.reviewer, expected_revision=second.revision)
        self.assertEqual(list(Question.objects.order_by("id").values()), before_questions)
        self.assertTrue(QuestionIdentityReservation.objects.filter(pk=protected_id).exists())
        for question in duplicates:
            claim = QuestionIdentityReservation.objects.get(question=question)
            self.assertEqual((claim.fingerprint, claim.bundle_fingerprint), (standalone_identity(question), ""))


class MalformedCaseAssessmentTests(FacultyCaseFixtureMixin, Stage4TestCase):
    def make_cycle(self, **kwargs):
        self.configurer = self.admin
        cycle = super().make_cycle(**kwargs)
        cycle.processing_mode = "AUTOMATIC_GENERATION"
        cycle.automatic_contributor_completion_policy = "SUFFICIENT_POOL"
        cycle.save()
        return cycle

    def make_course(self, **kwargs):
        from .setup_services import CourseSetupService
        course = super().make_course(**kwargs)
        CourseSetupService.classify(course_id=course.pk, tenant_id=self.tenant.id, actor=self.admin,
            classification="DEPARTMENTAL", expected_state=CourseSetupService.fingerprint(course))
        course.refresh_from_db()
        return course

    def test_malformed_case_excluded_with_sufficient_valid_pool_and_narrative_cache(self):
        from .tests_case_generation import CaseGenerationTests
        from .generation_readiness import Stage6ReadinessService
        from .models import ExamScenario, ExamScenarioMember
        cases = CaseGenerationTests.fill_and_submit(self)
        self.faculty, self.contribution = self.other_faculty, self.other_contribution
        replacement = self.save_case(html="<p>Replacement narrative</p>")
        for i in range(5):
            self.add_question(scenario=replacement, text=f"Replacement {i}")
        # Assessment fixture only: preserve 50 valid Submitted section/campus items.
        FacultyContribution.objects.filter(pk=self.contribution.pk).update(status="SUBMITTED", submitted_at=timezone.now())
        draft = FacultyContribution.objects.create(cycle_course=self.parent, faculty_user=self.make_faculty("irrelevant-draft"),
            source_campus=self.campus, quota_snapshot=50, configuration_revision_snapshot=1)
        irrelevant = ExamScenario.objects.create(blueprint=self.blueprint, contribution=draft, stimulus="<script>private draft</script>",
            content_format="RICH_HTML_V1", created_by=self.admin, updated_by=self.admin)
        draft_question = Question.objects.create(contribution=draft, position=1,
            **{**self.payload("Draft question"), "correct_answer": "D", "difficulty": "EASY"})
        ExamScenarioMember.objects.create(scenario=irrelevant, question=draft_question, position=1)
        ExamScenario.objects.filter(pk=cases[0].pk).update(stimulus="<script>private invalid narrative</script>")
        valid_ids = list(Question.objects.filter(contribution__cycle_course=self.parent,
            contribution__status="SUBMITTED").exclude(exam_scenario_membership__scenario=cases[0])
            .order_by("pk").values_list("pk", flat=True))
        # Complete the disposable pool's configured 15/25/10 difficulty margins.
        Question.objects.filter(pk__in=valid_ids[15:40]).update(difficulty="MODERATE")
        Question.objects.filter(pk__in=valid_ids[40:]).update(difficulty="DIFFICULT")
        from . import duplicate_contract
        with patch.object(duplicate_contract, "narrative_identity", wraps=duplicate_contract.narrative_identity) as identity:
            report = Stage6ReadinessService.evaluate_automatic_pool(cycle_course=self.parent, exact_feasibility=False)
        self.assertEqual(report["eligible_question_count"], 50)
        self.assertEqual(report["scenario_count"], 10)
        self.assertFalse(report["shortages"], report["shortages"])
        self.assertIn("UNUSABLE_CASE_EXCLUDED", [w["code"] for w in report["warnings"]])
        self.assertNotIn("private", str(report["warnings"]))
        self.assertLessEqual(identity.call_count, 3)  # one invalid, one shared valid, one replacement
        self.assertTrue(ExamScenario.objects.filter(pk=irrelevant.pk).exists())


class DuplicateMigrationTests(Stage5FixtureMixin, Stage4TransactionTestCase):
    def test_schema_upgrade_preserves_submitted_collisions_without_winners(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        parent, _ = self.make_stage5_course()
        faculty = self.make_faculty("legacy-duplicates")
        self.make_assignment(parent, faculty)
        self.initialize(parent)
        contribution = FacultyContribution.objects.get(faculty_user=faculty)
        for position in (1, 2):
            Question.objects.create(contribution=contribution, position=position,
                **{**self.payload("legacy collision"), "difficulty": "EASY", "correct_answer": "D"})
        FacultyContribution.objects.filter(pk=contribution.pk).update(status="SUBMITTED", submitted_at=timezone.now())
        before_questions = list(Question.objects.order_by("pk").values())
        before_contributions = list(FacultyContribution.objects.order_by("pk").values())
        previous = [("departmental_exams", "0029_question_import_target_section")]
        current = [("departmental_exams", "0031_question_rich_content")]
        try:
            MigrationExecutor(connection).migrate(previous)
            MigrationExecutor(connection).migrate(current)
            self.assertEqual(list(Question.objects.order_by("pk").values()), before_questions)
            self.assertEqual(list(FacultyContribution.objects.order_by("pk").values()), before_contributions)
            self.assertFalse(QuestionIdentityReservation.objects.exists())
            output = io.StringIO()
            call_command("duplicate_question_preflight", tenant_id=self.tenant.id,
                cycle_course_id=parent.id, stdout=output)
            self.assertEqual(json.loads(output.getvalue())["collision_count"], 1)
            self.assertFalse(QuestionIdentityReservation.objects.exists())
        finally:
            MigrationExecutor(connection).migrate(current)
