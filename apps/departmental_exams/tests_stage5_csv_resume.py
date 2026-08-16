import csv
import io
from pathlib import Path
from unittest import mock

from django.contrib.staticfiles import finders
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.http import Http404
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.auditlog.models import AuditLog

from .contribution_authorization import ContributionConflict
from .contribution_services import QuestionMutationService
from .csv_import import QuestionCSVImportService, QuestionCSVParser
from .models import FacultyContribution, Question, QuestionImportBatch
from .stage4_test_support import Stage4TestCase, Stage4TransactionTestCase
from .tests_stage5_contributions import Stage5FixtureMixin


@override_settings(STATICFILES_STORAGE="django.contrib.staticfiles.storage.StaticFilesStorage")
class Stage5ResumableCSVImportTests(Stage5FixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.parent, self.configuration = self.make_stage5_course()
        self.faculty = self.make_faculty("resumable-csv-faculty")
        self.assignment = self.make_assignment(self.parent, self.faculty)
        self.initialize(self.parent)
        self.contribution = FacultyContribution.objects.get(faculty_user=self.faculty)
        self.client.force_login(self.faculty)

    @staticmethod
    def row(index, text_prefix="Resumable question"):
        return [
            f"{text_prefix} {index}",
            "Choice A",
            "Choice B",
            "Choice C",
            "Choice D",
            "A",
            "EASY",
        ]

    @classmethod
    def upload(cls, count, *, text_prefix="Resumable question"):
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(
            (
                "question_text",
                "choice_a",
                "choice_b",
                "choice_c",
                "choice_d",
                "correct_answer",
                "difficulty",
            )
        )
        writer.writerows(cls.row(index, text_prefix) for index in range(1, count + 1))
        return SimpleUploadedFile(
            "questions.csv",
            stream.getvalue().encode("utf-8"),
            content_type="text/csv",
        )

    def create_preview(self, count=25, *, text_prefix="Resumable question"):
        return QuestionCSVImportService.create_preview(
            contribution_id=self.contribution.id,
            uploaded_file=self.upload(count, text_prefix=text_prefix),
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
        )

    def process(self, batch, *, chunk_size=10):
        return QuestionCSVImportService.process_next_chunk(
            token=batch.token,
            expected_file_sha256=batch.file_sha256,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            chunk_size=chunk_size,
        )[0]

    def finalized_questions(self):
        return Question.objects.filter(
            contribution=self.contribution,
            import_batch__status=QuestionImportBatch.Status.CONFIRMED,
        )

    def test_first_chunk_persists_authoritative_count_cursor_and_rows(self):
        batch = self.process(self.create_preview())
        batch.refresh_from_db()
        self.contribution.refresh_from_db()
        self.assertEqual(batch.status, QuestionImportBatch.Status.IMPORTING)
        self.assertEqual(batch.committed_rows, 10)
        self.assertEqual(batch.next_row_number, 12)
        self.assertIsNotNone(batch.started_at)
        self.assertIsNotNone(batch.progress_updated_at)
        self.assertEqual(Question.objects.filter(import_batch=batch).count(), 10)
        self.assertEqual(self.contribution.revision, batch.contribution_revision_snapshot)

    def test_chunked_import_completes_with_exact_count_positions_and_one_audit(self):
        batch = self.create_preview()
        self.process(batch)
        self.process(batch)
        completed = self.process(batch)
        completed.refresh_from_db()
        self.contribution.refresh_from_db()
        self.assertEqual(completed.status, QuestionImportBatch.Status.CONFIRMED)
        self.assertEqual(completed.committed_rows, 25)
        self.assertIsNone(completed.next_row_number)
        self.assertEqual(self.finalized_questions().count(), 25)
        self.assertEqual(
            list(self.finalized_questions().order_by("position").values_list("position", flat=True)),
            list(range(1, 26)),
        )
        self.assertFalse(completed.rows.exists())
        self.assertEqual(self.contribution.revision, completed.contribution_revision_snapshot + 1)
        self.assertEqual(
            AuditLog.objects.filter(action="DE_EXAM_QUESTION_CSV_IMPORTED").count(),
            1,
        )

    def test_same_completed_batch_replay_is_idempotent(self):
        batch = self.create_preview(3)
        completed, changed = QuestionCSVImportService.confirm(
            token=batch.token,
            expected_file_sha256=batch.file_sha256,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
        )
        replayed, replay_changed = QuestionCSVImportService.confirm(
            token=batch.token,
            expected_file_sha256=batch.file_sha256,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
        )
        self.assertTrue(changed)
        self.assertFalse(replay_changed)
        self.assertEqual(replayed.pk, completed.pk)
        self.assertEqual(Question.objects.filter(import_batch=batch).count(), 3)

    def test_response_loss_retry_continues_from_persisted_cursor_without_duplicates(self):
        batch = self.create_preview(21)
        self.process(batch)
        persisted = set(
            Question.objects.filter(import_batch=batch).values_list("import_row_number", flat=True)
        )
        self.assertEqual(len(persisted), 10)
        self.process(batch)
        completed = self.process(batch)
        row_numbers = list(
            Question.objects.filter(import_batch=batch)
            .order_by("import_row_number")
            .values_list("import_row_number", flat=True)
        )
        self.assertEqual(completed.status, QuestionImportBatch.Status.CONFIRMED)
        self.assertEqual(row_numbers, list(range(2, 23)))
        self.assertEqual(len(row_numbers), len(set(row_numbers)))

    def test_worker_interruption_pauses_and_resume_completes(self):
        batch = self.create_preview(11)
        self.process(batch)
        with mock.patch(
            "apps.departmental_exams.csv_import.Question.objects.bulk_create",
            side_effect=RuntimeError("simulated worker termination"),
        ):
            with self.assertRaises(RuntimeError):
                self.process(batch)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QuestionImportBatch.Status.PAUSED)
        self.assertTrue(batch.status in QuestionImportBatch.resumable_statuses())
        self.assertEqual(batch.committed_rows, 10)
        self.assertEqual(Question.objects.filter(import_batch=batch).count(), 10)
        completed = self.process(batch)
        self.assertEqual(completed.status, QuestionImportBatch.Status.CONFIRMED)
        self.assertEqual(Question.objects.filter(import_batch=batch).count(), 11)

    def test_final_chunk_audit_failure_rolls_back_boundary_and_can_resume(self):
        batch = self.create_preview(11)
        self.process(batch)
        with mock.patch(
            "apps.departmental_exams.csv_import.AuditService.log_event",
            side_effect=RuntimeError("simulated audit boundary failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.process(batch)
        batch.refresh_from_db()
        self.contribution.refresh_from_db()
        self.assertEqual(batch.status, QuestionImportBatch.Status.PAUSED)
        self.assertEqual(batch.committed_rows, 10)
        self.assertEqual(Question.objects.filter(import_batch=batch).count(), 10)
        self.assertEqual(self.contribution.revision, batch.contribution_revision_snapshot)
        completed = self.process(batch)
        self.assertEqual(completed.status, QuestionImportBatch.Status.CONFIRMED)
        self.assertEqual(Question.objects.filter(import_batch=batch).count(), 11)

    def test_stale_revision_terminates_cleans_partial_rows_and_releases_lock(self):
        batch = self.create_preview(11)
        self.process(batch)
        FacultyContribution.objects.filter(pk=self.contribution.pk).update(
            revision=self.contribution.revision + 1
        )
        with self.assertRaises(ContributionConflict):
            self.process(batch)
        batch.refresh_from_db()
        self.contribution.refresh_from_db()
        self.assertEqual(batch.status, QuestionImportBatch.Status.FAILED)
        self.assertEqual(batch.failure_code, "STALE_CONTRIBUTION")
        self.assertIn("start a fresh CSV preview", batch.failure_message)
        self.assertEqual(batch.committed_rows, 0)
        self.assertIsNone(batch.active_contribution_id)
        self.assertIsNotNone(batch.payload_purged_at)
        self.assertFalse(Question.objects.filter(import_batch=batch).exists())
        self.assertFalse(batch.rows.exists())
        replacement = self.create_preview(1, text_prefix="Fresh after stale")
        self.assertEqual(replacement.status, QuestionImportBatch.Status.READY)

    def test_quota_change_pauses_before_stale_revision_without_extra_question(self):
        Question.objects.bulk_create(
            [
                Question(
                    contribution=self.contribution,
                    question_text=f"Existing {position}",
                    choice_a="A",
                    choice_b="B",
                    choice_c="C",
                    choice_d="D",
                    correct_answer="A",
                    difficulty="EASY",
                    position=position,
                )
                for position in range(1, 50)
            ]
        )
        batch = self.create_preview(1)
        Question.objects.create(
            contribution=self.contribution,
            question_text="Quota filling question",
            choice_a="A",
            choice_b="B",
            choice_c="C",
            choice_d="D",
            correct_answer="A",
            difficulty="EASY",
            position=50,
        )
        FacultyContribution.objects.filter(pk=self.contribution.pk).update(
            revision=self.contribution.revision + 1
        )
        with self.assertRaises(ContributionConflict):
            self.process(batch)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QuestionImportBatch.Status.FAILED)
        self.assertEqual(batch.failure_code, "QUOTA_CHANGED")
        self.assertIn("start a fresh CSV preview", batch.failure_message)
        self.assertIsNone(batch.active_contribution_id)
        self.assertFalse(batch.rows.exists())
        self.assertEqual(Question.objects.count(), 50)
        self.contribution.refresh_from_db()
        filling_question = Question.objects.get(
            contribution=self.contribution,
            position=50,
        )
        QuestionMutationService.delete(
            contribution_id=self.contribution.id,
            question_id=filling_question.id,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
            expected_question_revision=filling_question.revision,
        )
        self.contribution.refresh_from_db()
        replacement = self.create_preview(1, text_prefix="Fresh after quota")
        self.assertEqual(replacement.status, QuestionImportBatch.Status.READY)

    def test_assignment_loss_terminates_cleans_partial_rows_and_releases_lock(self):
        batch = self.create_preview(11, text_prefix="PRIVATE ASSIGNMENT LOSS")
        self.process(batch)
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        with self.assertRaises(PermissionDenied):
            self.process(batch)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QuestionImportBatch.Status.FAILED)
        self.assertEqual(batch.failure_code, "AUTHORIZATION_CHANGED")
        self.assertNotIn("PRIVATE ASSIGNMENT LOSS", batch.failure_message)
        self.assertIn("start a fresh CSV preview", batch.failure_message)
        self.assertIsNone(batch.active_contribution_id)
        self.assertFalse(Question.objects.filter(import_batch=batch).exists())
        self.assertFalse(batch.rows.exists())
        self.assignment.is_active = True
        self.assignment.save(update_fields=["is_active", "updated_at"])
        replacement = self.create_preview(1, text_prefix="Fresh after authorization")
        self.assertEqual(replacement.status, QuestionImportBatch.Status.READY)

    def test_submitted_contribution_remains_immutable_on_resume(self):
        batch = self.create_preview(2)
        FacultyContribution.objects.filter(pk=self.contribution.pk).update(
            status=FacultyContribution.Status.SUBMITTED,
            submitted_at=timezone.now(),
        )
        with self.assertRaises(PermissionDenied):
            self.process(batch)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QuestionImportBatch.Status.FAILED)
        self.assertIsNone(batch.active_contribution_id)
        self.assertFalse(Question.objects.filter(import_batch=batch).exists())

    def test_cross_tenant_batch_access_is_owner_scoped_404(self):
        batch = self.create_preview(2)
        with self.assertRaises(Http404):
            QuestionCSVImportService.owner_batch(
                token=batch.token,
                user=self.faculty,
                tenant_id=self.other_tenant.id,
            )
        batch.refresh_from_db()
        self.assertEqual(batch.status, QuestionImportBatch.Status.READY)
        outsider = self.make_faculty("resume-outsider")
        for user, tenant_id in (
            (self.faculty, self.other_tenant.id),
            (outsider, self.tenant.id),
        ):
            with self.assertRaises(Http404):
                QuestionCSVImportService.process_next_chunk(
                    token=batch.token,
                    expected_file_sha256=batch.file_sha256,
                    user=user,
                    tenant_id=tenant_id,
                    campus_id=self.campus.id,
                )
        batch.refresh_from_db()
        self.assertEqual(batch.status, QuestionImportBatch.Status.READY)
        self.assertEqual(batch.committed_rows, 0)
        self.assertEqual(batch.failure_code, "")
        self.assertEqual(batch.failure_message, "")
        self.assertIsNone(batch.active_contribution_id)
        self.assertEqual(batch.rows.count(), 2)

    def test_forged_hash_http_404_leaves_ready_batch_unchanged(self):
        batch = self.create_preview(2)
        response = self.client.post(
            reverse("departmental_exams:csv_confirm", args=[batch.token]),
            {"file_sha256": "f" * 64},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 404)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QuestionImportBatch.Status.READY)
        self.assertEqual(batch.committed_rows, 0)
        self.assertIsNone(batch.next_row_number)
        self.assertIsNone(batch.active_contribution_id)
        self.assertIsNone(batch.started_at)
        self.assertIsNone(batch.progress_updated_at)
        self.assertEqual(batch.failure_code, "")
        self.assertEqual(batch.failure_message, "")
        self.assertTrue(batch.rows.exists())
        self.assertFalse(Question.objects.filter(import_batch=batch).exists())

    def test_second_preview_cannot_start_while_interrupted_batch_is_active(self):
        first = self.create_preview(11, text_prefix="First batch")
        second = self.create_preview(1, text_prefix="Second batch")
        self.process(first)
        with self.assertRaises(ContributionConflict):
            self.process(second)
        second.refresh_from_db()
        self.assertEqual(second.status, QuestionImportBatch.Status.READY)
        self.assertEqual(Question.objects.filter(import_batch=first).count(), 10)
        self.assertFalse(Question.objects.filter(import_batch=second).exists())

    def test_active_import_blocks_manual_question_mutation(self):
        batch = self.create_preview(11)
        self.process(batch)
        with self.assertRaises(PermissionDenied):
            QuestionMutationService.create(
                contribution_id=self.contribution.id,
                user=self.faculty,
                tenant_id=self.tenant.id,
                campus_id=self.campus.id,
                expected_contribution_revision=self.contribution.revision,
                payload=self.payload("Blocked manual question"),
            )
        self.assertEqual(Question.objects.filter(import_batch=batch).count(), 10)

    def test_workspace_discovers_active_import_and_hides_partial_question_content(self):
        batch = self.create_preview(11, text_prefix="PRIVATE PARTIAL")
        self.process(batch)
        response = self.client.get(
            reverse(
                "departmental_exams:contribution_workspace",
                args=[self.contribution.id],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An interrupted question import is available")
        self.assertContains(response, "10 / 11 rows committed")
        self.assertContains(response, "Resume import")
        self.assertContains(response, "0 / 50")
        self.assertNotContains(response, "PRIVATE PARTIAL")
        self.assertNotContains(response, ">Add question<")

    def test_status_endpoint_recovers_persisted_progress_after_refresh(self):
        batch = self.create_preview(11)
        self.process(batch)
        response = self.client.get(
            reverse("departmental_exams:csv_status", args=[batch.token]),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], QuestionImportBatch.Status.IMPORTING)
        self.assertEqual(response.json()["committed_rows"], 10)
        self.assertEqual(response.json()["total_rows"], 11)
        self.assertEqual(response.json()["percentage"], 91)
        self.assertTrue(response.json()["can_resume"])

    def test_async_confirm_commits_one_chunk_and_reports_persisted_percentage(self):
        batch = self.create_preview(25)
        response = self.client.post(
            reverse("departmental_exams:csv_confirm", args=[batch.token]),
            {"file_sha256": batch.file_sha256},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["committed_rows"], 10)
        self.assertEqual(response.json()["total_rows"], 25)
        self.assertEqual(response.json()["percentage"], 40)
        self.assertFalse(response.json()["completed"])

    def test_async_resume_after_lost_response_completes_without_duplicate_rows(self):
        batch = self.create_preview(11)
        confirm_url = reverse("departmental_exams:csv_confirm", args=[batch.token])
        self.client.post(
            confirm_url,
            {"file_sha256": batch.file_sha256},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        response = self.client.post(
            confirm_url,
            {"file_sha256": batch.file_sha256},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["completed"])
        row_numbers = list(
            Question.objects.filter(import_batch=batch).values_list(
                "import_row_number", flat=True
            )
        )
        self.assertEqual(len(row_numbers), 11)
        self.assertEqual(len(row_numbers), len(set(row_numbers)))

    def test_upload_and_preview_render_honest_progress_lock_and_reminder(self):
        upload_response = self.client.get(
            reverse("departmental_exams:csv_upload", args=[self.contribution.id])
        )
        self.assertContains(upload_response, "data-csv-upload-form")
        self.assertContains(upload_response, "Question CSV upload progress")
        self.assertContains(upload_response, "Question import is in progress")
        batch = self.create_preview(2)
        preview_response = self.client.get(
            reverse("departmental_exams:csv_preview", args=[batch.token])
        )
        self.assertContains(preview_response, "data-import-resume-form")
        self.assertContains(preview_response, "data-import-overlay")
        self.assertContains(preview_response, "Start resumable import")

    def test_frontend_script_has_upload_measurement_double_submit_and_advisory_unload(self):
        script_path = finders.find("js/departmental_exam_csv_import.js")
        self.assertIsNotNone(script_path)
        script = Path(script_path).read_text(encoding="utf-8")
        self.assertIn('xhr.upload.addEventListener("progress"', script)
        self.assertIn('phase.textContent = "Validating CSV..."', script)
        self.assertIn("error.textContent = message", script)
        self.assertIn("message.textContent = payload.failure_message", script)
        self.assertNotIn("innerHTML", script)
        self.assertNotIn("insertAdjacentHTML", script)
        self.assertIn("if (uploading) return", script)
        self.assertIn("if (running) return", script)
        self.assertIn('window.addEventListener("beforeunload"', script)
        self.assertIn('window.removeEventListener("beforeunload"', script)
        self.assertIn("lockPage(false)", script)

    def test_duplicate_warning_remains_nonblocking_with_row_identity(self):
        Question.objects.create(
            contribution=self.contribution,
            question_text="Duplicate warning question 1",
            choice_a="A",
            choice_b="B",
            choice_c="C",
            choice_d="D",
            correct_answer="A",
            difficulty="EASY",
            position=1,
        )
        batch = self.create_preview(1, text_prefix=" duplicate   warning question")
        self.assertEqual(batch.warning_count, 1)
        completed = self.process(batch)
        self.assertEqual(completed.status, QuestionImportBatch.Status.CONFIRMED)
        self.assertEqual(Question.objects.filter(contribution=self.contribution).count(), 2)

    def test_database_constraints_include_row_replay_and_active_import_guards(self):
        question_constraints = connection.introspection.get_constraints(
            connection.cursor(), Question._meta.db_table
        )
        batch_constraints = connection.introspection.get_constraints(
            connection.cursor(), QuestionImportBatch._meta.db_table
        )
        self.assertIn("uq_de_question_import_row", question_constraints)
        self.assertIn("ck_de_batch_progress", batch_constraints)
        self.assertIn("ck_de_batch_active_contrib", batch_constraints)
        self.assertTrue(
            QuestionImportBatch._meta.get_field("active_contribution").unique
        )

    def test_duplicate_import_row_identity_is_rejected_by_database(self):
        batch = self.create_preview(11)
        self.process(batch)
        existing = Question.objects.filter(import_batch=batch).first()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Question.objects.create(
                contribution=self.contribution,
                question_text="Forbidden replay",
                choice_a="A",
                choice_b="B",
                choice_c="C",
                choice_d="D",
                correct_answer="A",
                difficulty="EASY",
                position=existing.position + 100,
                entry_method=Question.EntryMethod.CSV,
                import_batch=batch,
                import_row_number=existing.import_row_number,
            )

    def test_cleanup_does_not_purge_interrupted_active_batch(self):
        batch = self.create_preview(11)
        self.process(batch)
        result = QuestionCSVImportService.owner_batch(
            token=batch.token,
            user=self.faculty,
            tenant_id=self.tenant.id,
        )
        self.assertEqual(result.status, QuestionImportBatch.Status.IMPORTING)
        from .csv_import import QuestionImportCleanupService

        cleanup = QuestionImportCleanupService.purge(
            now=timezone.now() + timezone.timedelta(days=2)
        )
        batch.refresh_from_db()
        self.assertEqual(cleanup["expired_batches"], 0)
        self.assertEqual(batch.status, QuestionImportBatch.Status.IMPORTING)
        self.assertTrue(batch.rows.exists())


class Stage5ResumableCSVReverseMigrationTests(
    Stage5FixtureMixin,
    Stage4TransactionTestCase,
):
    def setUp(self):
        super().setUp()
        self.parent, self.configuration = self.make_stage5_course()
        self.faculty = self.make_faculty("resumable-csv-reverse")
        self.make_assignment(self.parent, self.faculty)
        self.initialize(self.parent)
        self.contribution = FacultyContribution.objects.get(faculty_user=self.faculty)

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    @staticmethod
    def _target_state(executor, departmental_target):
        return [
            departmental_target if app_label == "departmental_exams" else node
            for node in executor.loader.graph.leaf_nodes()
            for app_label, _name in [node]
        ]

    def test_reverse_discards_partial_progress_and_restores_old_constraints(self):
        batch = QuestionCSVImportService.create_preview(
            contribution_id=self.contribution.id,
            uploaded_file=Stage5ResumableCSVImportTests.upload(11),
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
        )
        QuestionCSVImportService.process_next_chunk(
            token=batch.token,
            expected_file_sha256=batch.file_sha256,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
        )
        self.assertEqual(Question.objects.filter(import_batch=batch).count(), 10)

        executor = MigrationExecutor(connection)
        target = self._target_state(
            executor,
            ("departmental_exams", "0017_automatic_generation_audit_run"),
        )
        executor.migrate(target)
        old_apps = executor.loader.project_state(target).apps
        OldBatch = old_apps.get_model("departmental_exams", "QuestionImportBatch")
        OldQuestion = old_apps.get_model("departmental_exams", "Question")
        OldRow = old_apps.get_model("departmental_exams", "QuestionImportRow")
        reversed_batch = OldBatch.objects.get(pk=batch.pk)
        self.assertEqual(reversed_batch.status, "EXPIRED")
        self.assertIsNone(reversed_batch.confirmed_at)
        self.assertIsNotNone(reversed_batch.payload_purged_at)
        self.assertFalse(OldQuestion.objects.filter(import_batch_id=batch.pk).exists())
        self.assertFalse(OldRow.objects.filter(batch_id=batch.pk).exists())
