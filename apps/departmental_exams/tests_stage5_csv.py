import csv
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404

from apps.auditlog.models import AuditLog

from .contribution_authorization import ContributionQuotaReached
from .contribution_services import QuestionMutationService
from .csv_import import (
    CSV_FILENAME,
    CSV_HEADERS,
    QuestionCSVImportService,
    QuestionCSVParser,
)
from .models import FacultyContribution, Question, QuestionImportBatch, QuestionImportRow
from .tests_stage5_contributions import Stage5FixtureMixin
from .stage4_test_support import Stage4TestCase


class Stage5CSVTests(Stage5FixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.parent, self.configuration = self.make_stage5_course()
        self.faculty = self.make_faculty("csv-faculty")
        self.make_assignment(self.parent, self.faculty)
        self.initialize(self.parent)
        self.contribution = FacultyContribution.objects.get()

    @staticmethod
    def csv_upload(rows, *, headers=CSV_HEADERS, name="questions.csv", bom=False):
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)
        raw = stream.getvalue().encode("utf-8")
        if bom:
            raw = b"\xef\xbb\xbf" + raw
        return SimpleUploadedFile(name, raw, content_type="text/csv")

    @staticmethod
    def row(text="CSV question"):
        return [text, "A", "B", "C", "D", "a", "moderate"]

    def create_preview(self, upload):
        return QuestionCSVImportService.create_preview(
            contribution_id=self.contribution.id,
            uploaded_file=upload,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
        )

    def test_template_has_exact_filename_headers_and_labeled_sample(self):
        decoded = QuestionCSVImportService.template_bytes().decode("utf-8")
        rows = list(csv.reader(io.StringIO(decoded)))
        self.assertEqual(CSV_FILENAME, "TeacherMatePlus_Departmental_Exam_Questions.csv")
        self.assertEqual(tuple(rows[0]), CSV_HEADERS)
        self.assertIn("SAMPLE", rows[1][0])

    def test_utf8_bom_and_case_normalization_create_ready_preview(self):
        batch = self.create_preview(self.csv_upload([self.row()], bom=True))
        preview = batch.rows.get()
        self.assertEqual(batch.status, "READY")
        self.assertEqual(preview.payload["correct_answer"], "A")
        self.assertEqual(preview.payload["difficulty"], "MODERATE")

    def test_wrong_headers_internal_blank_and_trailing_blank_rules(self):
        wrong = self.create_preview(
            self.csv_upload([self.row()], headers=tuple(reversed(CSV_HEADERS)))
        )
        self.assertEqual(wrong.status, "INVALID")
        self.assertEqual(wrong.rows.get().row_number, 1)
        self.contribution.refresh_from_db()
        internal = self.create_preview(
            self.csv_upload([self.row("One"), ["", "", "", "", "", "", ""], self.row("Two")])
        )
        self.assertEqual(internal.status, "INVALID")
        self.assertTrue(internal.rows.get(row_number=3).errors)
        self.contribution.refresh_from_db()
        trailing = self.create_preview(
            self.csv_upload([self.row(), ["", "", "", "", "", "", ""]])
        )
        self.assertEqual(trailing.status, "READY")
        self.assertEqual(trailing.total_rows, 1)

    def test_parser_rejects_extension_encoding_size_empty_malformed_columns_and_row_limit(self):
        cases = [
            SimpleUploadedFile("questions.txt", b"not csv"),
            SimpleUploadedFile("questions.csv", b"\xff\xfe"),
            SimpleUploadedFile("questions.csv", b"x" * (2 * 1024 * 1024 + 1)),
            self.csv_upload([]),
            SimpleUploadedFile("questions.csv", (",".join(CSV_HEADERS) + "\n\"unterminated").encode()),
            self.csv_upload([["only", "two"]]),
            self.csv_upload([self.row(str(index)) for index in range(201)]),
        ]
        for upload in cases:
            with self.subTest(name=upload.name, size=upload.size):
                parsed = QuestionCSVParser.parse(upload)
                self.assertGreater(parsed.error_count, 0)

    def test_more_rows_than_remaining_quota_is_blocking(self):
        batch = self.create_preview(
            self.csv_upload([self.row(str(index)) for index in range(51)])
        )
        self.assertEqual(batch.status, "INVALID")
        self.assertGreater(batch.error_count, 0)

    def test_preview_creation_at_exact_quota_is_conflict_without_batch(self):
        Question.objects.bulk_create(
            [
                Question(
                    contribution=self.contribution,
                    question_text=f"Existing question {position}",
                    choice_a="A",
                    choice_b="B",
                    choice_c="C",
                    choice_d="D",
                    correct_answer="A",
                    difficulty="EASY",
                    position=position,
                )
                for position in range(1, 51)
            ]
        )

        with self.assertRaises(ContributionQuotaReached):
            self.create_preview(self.csv_upload([self.row("Forbidden CSV question")]))

        self.assertEqual(Question.objects.count(), 50)
        self.assertFalse(QuestionImportBatch.objects.exists())

    def test_duplicate_question_warning_is_nonblocking_and_owner_only(self):
        Question.objects.create(
            contribution=self.contribution,
            question_text="Same question",
            choice_a="A",
            choice_b="B",
            choice_c="C",
            choice_d="D",
            correct_answer="A",
            difficulty="EASY",
            position=1,
        )
        batch = self.create_preview(self.csv_upload([self.row(" same   question ")]))
        self.assertEqual(batch.status, "READY")
        self.assertEqual(batch.warning_count, 1)
        self.assertNotIn("Same question", str(batch.rows.get().warnings))

    def test_atomic_confirm_appends_purges_payload_and_replays_idempotently(self):
        batch = self.create_preview(
            self.csv_upload([self.row("First CSV"), self.row("Second CSV")])
        )
        confirmed, changed = QuestionCSVImportService.confirm(
            token=batch.token,
            expected_file_sha256=batch.file_sha256,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
        )
        self.assertTrue(changed)
        self.assertEqual(Question.objects.count(), 2)
        self.assertEqual(
            list(Question.objects.order_by("position").values_list("position", "entry_method")),
            [(1, "CSV"), (2, "CSV")],
        )
        self.assertFalse(QuestionImportRow.objects.filter(batch=batch).exists())
        self.assertIsNotNone(confirmed.payload_purged_at)
        _confirmed, replay_changed = QuestionCSVImportService.confirm(
            token=batch.token,
            expected_file_sha256=batch.file_sha256,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
        )
        self.assertFalse(replay_changed)
        self.assertEqual(Question.objects.count(), 2)

    def test_second_preview_becomes_stale_after_first_confirmation(self):
        first = self.create_preview(self.csv_upload([self.row("First")]))
        second = self.create_preview(self.csv_upload([self.row("Second")]))
        QuestionCSVImportService.confirm(
            token=first.token,
            expected_file_sha256=first.file_sha256,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
        )
        with self.assertRaises(Exception) as captured:
            QuestionCSVImportService.confirm(
                token=second.token,
                expected_file_sha256=second.file_sha256,
                user=self.faculty,
                tenant_id=self.tenant.id,
                campus_id=self.campus.id,
            )
        self.assertIn("stale", str(captured.exception).lower())
        self.assertEqual(Question.objects.count(), 1)

    def test_preview_confirmation_at_newly_full_quota_is_denied_atomically(self):
        Question.objects.bulk_create(
            [
                Question(
                    contribution=self.contribution,
                    question_text=f"Existing question {position}",
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
        batch = self.create_preview(self.csv_upload([self.row("Preview question")]))
        QuestionMutationService.create(
            contribution_id=self.contribution.id,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
            payload=self.payload("Question that fills the quota"),
        )
        self.contribution.refresh_from_db()
        revision_after_fill = self.contribution.revision
        audit_count = AuditLog.objects.filter(
            action="DE_EXAM_QUESTION_CSV_IMPORTED"
        ).count()

        with self.assertRaises(ContributionQuotaReached):
            QuestionCSVImportService.confirm(
                token=batch.token,
                expected_file_sha256=batch.file_sha256,
                user=self.faculty,
                tenant_id=self.tenant.id,
                campus_id=self.campus.id,
            )

        self.contribution.refresh_from_db()
        batch.refresh_from_db()
        self.assertEqual(self.contribution.questions.count(), 50)
        self.assertEqual(self.contribution.revision, revision_after_fill)
        self.assertEqual(batch.status, QuestionImportBatch.Status.READY)
        self.assertTrue(batch.rows.exists())
        self.assertEqual(
            AuditLog.objects.filter(action="DE_EXAM_QUESTION_CSV_IMPORTED").count(),
            audit_count,
        )

    def test_wrong_owner_and_forged_file_hash_are_owner_scoped_404(self):
        batch = self.create_preview(self.csv_upload([self.row()]))
        outsider = self.make_faculty("csv-outsider")
        with self.assertRaises(Http404):
            QuestionCSVImportService.owner_batch(
                token=batch.token,
                user=outsider,
                tenant_id=self.tenant.id,
            )
        with self.assertRaises(Http404):
            QuestionCSVImportService.confirm(
                token=batch.token,
                expected_file_sha256="f" * 64,
                user=self.faculty,
                tenant_id=self.tenant.id,
                campus_id=self.campus.id,
            )

    def test_error_rows_never_repeat_confidential_content(self):
        batch = self.create_preview(self.csv_upload([["SECRET TEXT", "A", "A", "C", "D", "A", "Easy"]]))
        self.assertEqual(batch.status, "INVALID")
        rendered_errors = str(batch.rows.get().errors)
        self.assertNotIn("SECRET TEXT", rendered_errors)
