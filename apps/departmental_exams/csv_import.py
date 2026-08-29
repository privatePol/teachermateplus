from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass, field
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404
from django.utils import timezone

from apps.core.services.audit import AuditService
from apps.core.services.features import FeatureSettingsService

from .contribution_authorization import (
    ContributionAuthorizationService,
    ContributionConflict,
    ContributionExpired,
    ContributionQuotaReached,
)
from .contribution_services import (
    QuestionPayloadService,
    Stage5LockService,
)
from .models import Question, QuestionImportBatch, QuestionImportRow


CSV_HEADERS = (
    "question_text",
    "choice_a",
    "choice_b",
    "choice_c",
    "choice_d",
    "correct_answer",
    "difficulty",
)
CSV_FILENAME = "TeacherMatePlus_Departmental_Exam_Questions.csv"
CSV_MAX_BYTES = 2 * 1024 * 1024
CSV_MAX_ROWS = 200
IMPORT_CHUNK_SIZE = 10
PREVIEW_LIFETIME = timedelta(minutes=30)
SHELL_RETENTION = timedelta(days=30)
CSV_SANITIZATION_WARNING = (
    "Hidden formatting characters were automatically cleaned from this row."
)


def sanitize_csv_question_text(value):
    """Clean harmless formatting artifacts from one parsed CSV text field."""
    sanitized = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    sanitized = sanitized.replace("\v", "\n").replace("\f", "\n")
    sanitized = sanitized.expandtabs(4)
    return (
        sanitized.replace("\u00a0", " ")
        .replace("\u200b", "")
        .replace("\ufeff", "")
    )


def sanitize_csv_question_payload(payload):
    sanitized = dict(payload)
    changed = False
    for field_name in QuestionPayloadService.TEXT_FIELDS:
        original = payload.get(field_name) or ""
        cleaned = sanitize_csv_question_text(original)
        sanitized[field_name] = cleaned
        changed = changed or cleaned != original
    return sanitized, changed


@dataclass
class ParsedImportRow:
    row_number: int
    payload: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    fingerprint: str = ""


@dataclass
class ParsedImport:
    raw_sha256: str
    filename_sha256: str
    rows: list

    @property
    def error_count(self):
        return sum(len(row.errors) for row in self.rows)

    @property
    def warning_count(self):
        return sum(len(row.warnings) for row in self.rows)

    @property
    def data_rows(self):
        return [row for row in self.rows if row.row_number >= 2]


class QuestionCSVParser:
    @staticmethod
    def _error(row_number, field_name, message):
        return ParsedImportRow(
            row_number=row_number,
            errors=[{"field": field_name, "message": message}],
        )

    @classmethod
    def parse(cls, uploaded_file):
        filename = uploaded_file.name or ""
        filename_hash = hashlib.sha256(filename.encode("utf-8")).hexdigest()
        if not filename.lower().endswith(".csv"):
            return ParsedImport(
                raw_sha256=hashlib.sha256(b"").hexdigest(),
                filename_sha256=filename_hash,
                rows=[cls._error(1, "file", "Upload a file with a .csv extension.")],
            )
        if uploaded_file.size > CSV_MAX_BYTES:
            return ParsedImport(
                raw_sha256=hashlib.sha256(b"").hexdigest(),
                filename_sha256=filename_hash,
                rows=[cls._error(1, "file", "CSV files may not exceed 2 MB.")],
            )
        raw = uploaded_file.read()
        raw_hash = hashlib.sha256(raw).hexdigest()
        if len(raw) > CSV_MAX_BYTES:
            return ParsedImport(
                raw_sha256=raw_hash,
                filename_sha256=filename_hash,
                rows=[cls._error(1, "file", "CSV files may not exceed 2 MB.")],
            )
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            return ParsedImport(
                raw_sha256=raw_hash,
                filename_sha256=filename_hash,
                rows=[cls._error(1, "file", "The CSV must use UTF-8 encoding.")],
            )
        try:
            parsed_rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
        except csv.Error:
            return ParsedImport(
                raw_sha256=raw_hash,
                filename_sha256=filename_hash,
                rows=[cls._error(1, "file", "The CSV is malformed and could not be parsed safely.")],
            )
        if not parsed_rows:
            return ParsedImport(
                raw_sha256=raw_hash,
                filename_sha256=filename_hash,
                rows=[cls._error(1, "header", "The exact CSV header row is required.")],
            )
        if tuple(parsed_rows[0]) != CSV_HEADERS:
            return ParsedImport(
                raw_sha256=raw_hash,
                filename_sha256=filename_hash,
                rows=[
                    cls._error(
                        1,
                        "header",
                        "Headers must exactly match the template spelling, order, case, and spacing.",
                    )
                ],
            )

        raw_data_rows = parsed_rows[1:]
        last_nonblank = -1
        for index, values in enumerate(raw_data_rows):
            if any((value or "").strip() for value in values):
                last_nonblank = index
        if last_nonblank < 0:
            return ParsedImport(
                raw_sha256=raw_hash,
                filename_sha256=filename_hash,
                rows=[cls._error(2, "row", "The CSV contains no data rows.")],
            )
        raw_data_rows = raw_data_rows[: last_nonblank + 1]
        nonblank_count = sum(
            1 for values in raw_data_rows if any((value or "").strip() for value in values)
        )
        rows = []
        if nonblank_count > CSV_MAX_ROWS:
            rows.append(
                cls._error(1, "file", "The CSV may contain at most 200 nonblank data rows.")
            )
        for offset, values in enumerate(raw_data_rows, start=2):
            if not any((value or "").strip() for value in values):
                rows.append(cls._error(offset, "row", "Internal blank rows are not allowed."))
                continue
            if len(values) != len(CSV_HEADERS):
                rows.append(
                    cls._error(
                        offset,
                        "row",
                        f"Expected {len(CSV_HEADERS)} columns but received {len(values)}.",
                    )
                )
                continue
            raw_payload = dict(zip(CSV_HEADERS, values))
            sanitized_payload, sanitation_changed = sanitize_csv_question_payload(
                raw_payload
            )
            row = ParsedImportRow(row_number=offset)
            if sanitation_changed:
                row.warnings.append(
                    {"field": "row", "message": CSV_SANITIZATION_WARNING}
                )
            try:
                row.payload = QuestionPayloadService.validate(sanitized_payload)
                row.fingerprint = QuestionPayloadService.question_fingerprint(
                    row.payload["question_text"]
                )
            except ValidationError as exc:
                if hasattr(exc, "message_dict"):
                    for field_name, messages in exc.message_dict.items():
                        for message in messages:
                            row.errors.append(
                                {"field": field_name, "message": str(message)}
                            )
                else:
                    for message in exc.messages:
                        row.errors.append({"field": "row", "message": str(message)})
                contains_unsupported_text = any(
                    QuestionPayloadService.has_unsupported_characters(
                        sanitized_payload.get(field_name)
                    )
                    for field_name in QuestionPayloadService.TEXT_FIELDS
                )
                row.payload = (
                    {}
                    if contains_unsupported_text
                    else {
                        field_name: QuestionPayloadService.normalize_text(
                            sanitized_payload.get(field_name)
                        )
                        for field_name in CSV_HEADERS
                    }
                )
            rows.append(row)
        return ParsedImport(
            raw_sha256=raw_hash,
            filename_sha256=filename_hash,
            rows=rows,
        )


class QuestionCSVImportService:
    SAFE_FAILURE_MESSAGES = {
        "AUTHORIZATION_CHANGED": "Import stopped because current access or assignment eligibility changed. Partial imported rows were discarded; start a fresh CSV preview when access is restored.",
        "STALE_CONTRIBUTION": "Import stopped because the contribution changed after preview. Partial imported rows were discarded; start a fresh CSV preview.",
        "QUOTA_CHANGED": "Import stopped because the available question quota changed. Partial imported rows were discarded; review the workspace and start a fresh CSV preview.",
        "INVALID_IMPORT_STATE": "Import stopped because its persisted state could not be validated safely. Partial imported rows were discarded; start a fresh CSV preview.",
        "PROCESSING_INTERRUPTED": "Import paused after an unexpected processing interruption. Persisted progress is safe to retry.",
    }

    @staticmethod
    def template_bytes():
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\r\n")
        writer.writerow(CSV_HEADERS)
        writer.writerow(
            [
                "SAMPLE - replace or delete this row before upload",
                "Sample choice A",
                "Sample choice B",
                "Sample choice C",
                "Sample choice D",
                "A",
                "Easy",
            ]
        )
        return output.getvalue().encode("utf-8")

    @staticmethod
    def _owner_batch_identity(*, token, user, tenant_id):
        identity = QuestionImportBatch.objects.filter(
            token=token,
            tenant_id=tenant_id,
            uploading_user=user,
            contribution__faculty_user=user,
        ).values("id", "contribution_id").first()
        if identity is None:
            raise Http404
        return identity

    @classmethod
    @transaction.atomic
    def create_preview(
        cls,
        *,
        contribution_id,
        uploaded_file,
        user,
        tenant_id,
        campus_id,
        expected_contribution_revision,
    ):
        parsed = QuestionCSVParser.parse(uploaded_file)
        _cycle, _course, configuration, contribution = Stage5LockService.lock_contribution(
            contribution_id=contribution_id,
            user=user,
            tenant_id=tenant_id,
        )
        ContributionAuthorizationService.require_mutable_locked(
            user=user,
            contribution=contribution,
            configuration=configuration,
            request_tenant_id=tenant_id,
            request_campus_id=campus_id,
        )
        ContributionAuthorizationService.require_no_active_import(
            contribution=contribution,
        )
        ContributionAuthorizationService.require_revision(
            contribution=contribution,
            expected_revision=expected_contribution_revision,
        )
        # The locked contribution revision serializes Stage 5 writers. Preview
        # creation does not lock question rows before its newly created batch,
        # preserving the canonical parent -> contribution -> batch order.
        existing_questions = list(
            Question.objects.filter(contribution=contribution).order_by("pk")
        )
        ContributionAuthorizationService.require_add_capacity(
            contribution=contribution,
            question_count=len(existing_questions),
        )
        remaining = contribution.quota_snapshot - len(existing_questions)
        data_rows = parsed.data_rows
        if len(data_rows) > remaining:
            parsed.rows.insert(
                0,
                QuestionCSVParser._error(
                    1,
                    "quota",
                    f"The CSV has {len(data_rows)} rows but only {remaining} quota slots remain.",
                ),
            )
        existing_fingerprints = {
            QuestionPayloadService.question_fingerprint(
                sanitize_csv_question_text(value)
            )
            for value in Question.objects.filter(
                contribution__faculty_user=user
            ).values_list("question_text", flat=True)
        }
        seen_fingerprints = set()
        for row in data_rows:
            if row.errors or not row.fingerprint:
                continue
            if row.fingerprint in existing_fingerprints:
                row.warnings.append(
                    {
                        "field": "question_text",
                        "message": "This question resembles one already saved in your contribution.",
                    }
                )
            if row.fingerprint in seen_fingerprints:
                row.warnings.append(
                    {
                        "field": "question_text",
                        "message": "This question resembles another row in this CSV.",
                    }
                )
            seen_fingerprints.add(row.fingerprint)

        error_count = parsed.error_count
        warning_count = parsed.warning_count
        valid_rows = sum(1 for row in data_rows if not row.errors)
        status = (
            QuestionImportBatch.Status.READY
            if error_count == 0 and valid_rows > 0
            else QuestionImportBatch.Status.INVALID
        )
        batch = QuestionImportBatch.objects.create(
            tenant_id=tenant_id,
            contribution=contribution,
            uploading_user=user,
            status=status,
            source_format=QuestionImportBatch.SourceFormat.CSV,
            contribution_revision_snapshot=contribution.revision,
            file_sha256=parsed.raw_sha256,
            filename_sha256=parsed.filename_sha256,
            total_rows=len(data_rows),
            valid_rows=valid_rows,
            error_count=error_count,
            warning_count=warning_count,
            resulting_question_count=len(existing_questions) + valid_rows,
            expires_at=timezone.now() + PREVIEW_LIFETIME,
        )
        QuestionImportRow.objects.bulk_create(
            [
                QuestionImportRow(
                    batch=batch,
                    row_number=row.row_number,
                    payload=row.payload,
                    errors=row.errors,
                    warnings=row.warnings,
                    fingerprint=row.fingerprint,
                )
                for row in parsed.rows
            ],
            batch_size=CSV_MAX_ROWS,
        )
        return batch

    @classmethod
    def owner_batch(cls, *, token, user, tenant_id):
        cls._owner_batch_identity(token=token, user=user, tenant_id=tenant_id)
        batch = (
            QuestionImportBatch.objects.filter(
                token=token,
                tenant_id=tenant_id,
                uploading_user=user,
                contribution__faculty_user=user,
            )
            .select_related(
                "contribution",
                "contribution__cycle_course",
                "contribution__cycle_course__cycle",
                "contribution__cycle_course__cycle__tenant",
                "contribution__cycle_course__course",
                "contribution__cycle_course__configuration",
            )
            .prefetch_related("rows")
            .first()
        )
        if batch is None:
            raise Http404
        if (
            batch.status in {QuestionImportBatch.Status.READY, QuestionImportBatch.Status.INVALID}
            and timezone.now() >= batch.expires_at
        ):
            raise ContributionExpired("This confidential preview has expired. Upload the CSV again.")
        return batch

    @classmethod
    def active_batch_for_contribution(cls, *, contribution, user, tenant_id):
        return (
            QuestionImportBatch.objects.filter(
                contribution=contribution,
                tenant_id=tenant_id,
                uploading_user=user,
                status__in=QuestionImportBatch.active_statuses(),
            )
            .order_by("created_at", "id")
            .first()
        )

    @staticmethod
    def status_payload(batch):
        percentage = (
            round((batch.committed_rows / batch.total_rows) * 100)
            if batch.total_rows
            else 0
        )
        return {
            "status": batch.status,
            "committed_rows": batch.committed_rows,
            "total_rows": batch.total_rows,
            "percentage": percentage,
            "can_resume": batch.status in QuestionImportBatch.resumable_statuses(),
            "completed": batch.status == QuestionImportBatch.Status.CONFIRMED,
            "failure_code": batch.failure_code,
            "failure_message": batch.failure_message,
            "contribution_id": batch.contribution_id,
        }

    @classmethod
    def _locked_import_state(
        cls,
        *,
        token,
        expected_file_sha256,
        user,
        tenant_id,
        campus_id,
    ):
        identity = cls._owner_batch_identity(token=token, user=user, tenant_id=tenant_id)
        _cycle, _course, configuration, contribution = Stage5LockService.lock_contribution(
            contribution_id=identity["contribution_id"],
            user=user,
            tenant_id=tenant_id,
        )
        batch = QuestionImportBatch.objects.select_for_update().filter(
            pk=identity["id"],
            token=token,
            tenant_id=tenant_id,
            uploading_user=user,
            contribution=contribution,
        ).first()
        if batch is None or batch.file_sha256 != expected_file_sha256:
            raise Http404
        if (
            batch.source_format == QuestionImportBatch.SourceFormat.DOCX
            and not FeatureSettingsService.is_departmental_exam_docx_import_enabled(
                tenant_id=tenant_id, default=False
            )
        ):
            raise PermissionDenied("Word question import is disabled for this tenant.")
        if batch.status == QuestionImportBatch.Status.CONFIRMED:
            return configuration, contribution, batch, [], [], []
        if batch.status == QuestionImportBatch.Status.FAILED:
            if batch.failure_code == "QUOTA_CHANGED":
                raise ContributionQuotaReached(contribution.quota_snapshot)
            if batch.failure_code == "STALE_CONTRIBUTION":
                raise ContributionConflict(batch.failure_message)
            if batch.failure_code == "AUTHORIZATION_CHANGED":
                raise PermissionDenied(batch.failure_message)
            raise ValidationError(batch.failure_message or "The import cannot be resumed safely.")
        if (
            batch.status in {QuestionImportBatch.Status.READY, QuestionImportBatch.Status.INVALID}
            and timezone.now() >= batch.expires_at
        ):
            raise ContributionExpired("This confidential preview has expired. Upload the CSV again.")
        if batch.status not in QuestionImportBatch.resumable_statuses() or batch.error_count:
            raise ValidationError("Only a valid resumable preview can be imported.")
        ContributionAuthorizationService.require_mutable_locked(
            user=user,
            contribution=contribution,
            configuration=configuration,
            request_tenant_id=tenant_id,
            request_campus_id=campus_id,
        )
        if batch.status == QuestionImportBatch.Status.READY and QuestionImportBatch.objects.filter(
            contribution=contribution,
            status__in=QuestionImportBatch.active_statuses(),
        ).exclude(pk=batch.pk).exists():
            raise ContributionConflict(
                "Another interrupted question import must be completed before this preview can start."
            )
        rows = list(
            QuestionImportRow.objects.select_for_update()
            .filter(batch=batch)
            .order_by("row_number")
        )
        if len(rows) != batch.total_rows or any(row.errors for row in rows):
            raise ValidationError("The persisted preview rows are not valid for import.")
        imported_questions = list(
            Question.objects.select_for_update()
            .filter(contribution=contribution, import_batch=batch)
            .order_by("import_row_number", "pk")
        )
        other_questions = list(
            Question.objects.select_for_update()
            .filter(contribution=contribution)
            .exclude(import_batch=batch)
            .order_by("position", "pk")
        )
        imported_row_numbers = [item.import_row_number for item in imported_questions]
        staged_row_numbers = [row.row_number for row in rows]
        if (
            len(imported_questions) != batch.committed_rows
            or any(row_number is None for row_number in imported_row_numbers)
            or len(imported_row_numbers) != len(set(imported_row_numbers))
            or not set(imported_row_numbers).issubset(staged_row_numbers)
        ):
            raise ValidationError("The persisted committed-row progress is inconsistent.")
        ContributionAuthorizationService.require_add_capacity(
            contribution=contribution,
            question_count=len(other_questions),
        )
        if len(other_questions) + batch.total_rows > contribution.quota_snapshot:
            raise ContributionConflict(
                "The available question quota changed after this preview was created."
            )
        if contribution.revision != batch.contribution_revision_snapshot:
            raise ContributionConflict(
                "This preview is stale because the contribution changed. Upload the CSV again."
            )
        return configuration, contribution, batch, rows, imported_questions, other_questions

    @classmethod
    @transaction.atomic
    def _process_next_chunk_atomic(
        cls,
        *,
        token,
        expected_file_sha256,
        user,
        tenant_id,
        campus_id,
        request=None,
        chunk_size=IMPORT_CHUNK_SIZE,
    ):
        (
            _configuration,
            contribution,
            batch,
            rows,
            imported_questions,
            other_questions,
        ) = cls._locked_import_state(
            token=token,
            expected_file_sha256=expected_file_sha256,
            user=user,
            tenant_id=tenant_id,
            campus_id=campus_id,
        )
        if batch.status == QuestionImportBatch.Status.CONFIRMED:
            return batch, 0
        imported_row_numbers = {item.import_row_number for item in imported_questions}
        pending_rows = [row for row in rows if row.row_number not in imported_row_numbers]
        if not pending_rows:
            raise ValidationError("The persisted import has no remaining rows but is not complete.")
        chunk_rows = pending_rows[: max(1, min(int(chunk_size), CSV_MAX_ROWS))]
        row_positions = {
            row.row_number: len(other_questions) + index
            for index, row in enumerate(rows, start=1)
        }
        cleaned_rows = [
            (row, QuestionPayloadService.validate(row.payload)) for row in chunk_rows
        ]
        Question.objects.bulk_create(
            [
                Question(
                    contribution=contribution,
                    position=row_positions[row.row_number],
                    revision=1,
                    entry_method=(
                        Question.EntryMethod.DOCX
                        if batch.source_format == QuestionImportBatch.SourceFormat.DOCX
                        else Question.EntryMethod.CSV
                    ),
                    import_batch=batch,
                    import_row_number=row.row_number,
                    **payload,
                )
                for row, payload in cleaned_rows
            ],
            batch_size=IMPORT_CHUNK_SIZE,
        )
        committed_rows = len(imported_questions) + len(chunk_rows)
        now = timezone.now()
        batch.started_at = batch.started_at or now
        batch.progress_updated_at = now
        batch.committed_rows = committed_rows
        batch.failure_code = ""
        batch.failure_message = ""
        remaining_rows = pending_rows[len(chunk_rows):]
        if remaining_rows:
            batch.status = QuestionImportBatch.Status.IMPORTING
            batch.active_contribution = contribution
            batch.next_row_number = remaining_rows[0].row_number
            batch.save(update_fields=[
                "status",
                "active_contribution",
                "committed_rows",
                "next_row_number",
                "started_at",
                "progress_updated_at",
                "failure_code",
                "failure_message",
                "updated_at",
            ])
            return batch, len(chunk_rows)

        revision_before = contribution.revision
        contribution.revision += 1
        contribution.save(update_fields=["revision", "updated_at"])
        batch.status = QuestionImportBatch.Status.CONFIRMED
        batch.active_contribution = None
        batch.next_row_number = None
        batch.confirming_user = user
        batch.confirmed_at = now
        batch.payload_purged_at = now
        batch.save(update_fields=[
            "status",
            "active_contribution",
            "committed_rows",
            "next_row_number",
            "started_at",
            "progress_updated_at",
            "failure_code",
            "failure_message",
            "confirming_user",
            "confirmed_at",
            "payload_purged_at",
            "updated_at",
        ])
        QuestionImportRow.objects.filter(batch=batch).delete()
        all_imported = list(
            Question.objects.filter(import_batch=batch).order_by("import_row_number", "pk")
        )
        difficulty_counts = {}
        for question in all_imported:
            difficulty_counts[question.difficulty] = (
                difficulty_counts.get(question.difficulty, 0) + 1
            )
        AuditService.log_event(
            action=(
                "DE_EXAM_QUESTION_DOCX_IMPORTED"
                if batch.source_format == QuestionImportBatch.SourceFormat.DOCX
                else "DE_EXAM_QUESTION_CSV_IMPORTED"
            ),
            portal="FACULTY",
            entity_type="FacultyContribution",
            entity_id=contribution.id,
            actor=user,
            tenant=tenant_id,
            campus=contribution.source_campus_id,
            metadata={
                "cycle_id": contribution.cycle_course.cycle_id,
                "cycle_course_id": contribution.cycle_course_id,
                "batch_id": batch.id,
                "source_format": batch.source_format,
                "token_sha256": hashlib.sha256(str(batch.token).encode("ascii")).hexdigest(),
                "filename_sha256": batch.filename_sha256,
                "row_count": batch.total_rows,
                "warning_count": batch.warning_count,
                "quota": contribution.quota_snapshot,
                "resulting_count": len(other_questions) + batch.total_rows,
                "revision_before": revision_before,
                "revision_after": contribution.revision,
                "difficulty_counts": difficulty_counts,
            },
            request=request,
        )
        return batch, len(chunk_rows)

    @classmethod
    @transaction.atomic
    def _record_resumable_failure(
        cls,
        *,
        token,
        user,
        tenant_id,
        failure_code,
    ):
        identity = cls._owner_batch_identity(token=token, user=user, tenant_id=tenant_id)
        _cycle, _course, _configuration, contribution = Stage5LockService.lock_contribution(
            contribution_id=identity["contribution_id"],
            user=user,
            tenant_id=tenant_id,
        )
        batch = QuestionImportBatch.objects.select_for_update().filter(
            pk=identity["id"],
            contribution=contribution,
            uploading_user=user,
            tenant_id=tenant_id,
        ).first()
        if batch is None or batch.status in {
            QuestionImportBatch.Status.CONFIRMED,
            QuestionImportBatch.Status.EXPIRED,
            QuestionImportBatch.Status.INVALID,
            QuestionImportBatch.Status.FAILED,
        }:
            return
        if batch.status == QuestionImportBatch.Status.READY and QuestionImportBatch.objects.filter(
            contribution=contribution,
            status__in=QuestionImportBatch.active_statuses(),
        ).exclude(pk=batch.pk).exists():
            return
        committed_row_numbers = set(
            Question.objects.filter(import_batch=batch).values_list(
                "import_row_number", flat=True
            )
        )
        next_row_number = (
            QuestionImportRow.objects.filter(batch=batch)
            .exclude(row_number__in=committed_row_numbers)
            .order_by("row_number")
            .values_list("row_number", flat=True)
            .first()
        )
        if next_row_number is None:
            return
        now = timezone.now()
        batch.status = QuestionImportBatch.Status.PAUSED
        batch.active_contribution = contribution
        batch.committed_rows = len(committed_row_numbers)
        batch.next_row_number = next_row_number
        batch.started_at = batch.started_at or now
        batch.progress_updated_at = now
        batch.failure_code = failure_code
        batch.failure_message = cls.SAFE_FAILURE_MESSAGES[failure_code]
        batch.save(update_fields=[
            "status",
            "active_contribution",
            "committed_rows",
            "next_row_number",
            "started_at",
            "progress_updated_at",
            "failure_code",
            "failure_message",
            "updated_at",
        ])

    @classmethod
    @transaction.atomic
    def _record_terminal_failure(
        cls,
        *,
        token,
        user,
        tenant_id,
        failure_code,
    ):
        identity = cls._owner_batch_identity(token=token, user=user, tenant_id=tenant_id)
        _cycle, _course, _configuration, contribution = Stage5LockService.lock_contribution(
            contribution_id=identity["contribution_id"],
            user=user,
            tenant_id=tenant_id,
        )
        batch = QuestionImportBatch.objects.select_for_update().filter(
            pk=identity["id"],
            contribution=contribution,
            uploading_user=user,
            tenant_id=tenant_id,
        ).first()
        if batch is None or batch.status in {
            QuestionImportBatch.Status.CONFIRMED,
            QuestionImportBatch.Status.EXPIRED,
            QuestionImportBatch.Status.INVALID,
            QuestionImportBatch.Status.FAILED,
        }:
            return
        if batch.status == QuestionImportBatch.Status.READY and QuestionImportBatch.objects.filter(
            contribution=contribution,
            status__in=QuestionImportBatch.active_statuses(),
        ).exclude(pk=batch.pk).exists():
            return
        list(
            Question.objects.select_for_update()
            .filter(contribution=contribution, import_batch=batch)
            .order_by("pk")
        )
        Question.objects.filter(contribution=contribution, import_batch=batch).delete()
        QuestionImportRow.objects.filter(batch=batch).delete()
        now = timezone.now()
        batch.status = QuestionImportBatch.Status.FAILED
        batch.active_contribution = None
        batch.committed_rows = 0
        batch.next_row_number = None
        batch.started_at = batch.started_at or now
        batch.progress_updated_at = now
        batch.confirmed_at = None
        batch.payload_purged_at = now
        batch.failure_code = failure_code
        batch.failure_message = cls.SAFE_FAILURE_MESSAGES[failure_code]
        batch.save(update_fields=[
            "status",
            "active_contribution",
            "committed_rows",
            "next_row_number",
            "started_at",
            "progress_updated_at",
            "confirmed_at",
            "payload_purged_at",
            "failure_code",
            "failure_message",
            "updated_at",
        ])

    @classmethod
    def process_next_chunk(cls, **kwargs):
        try:
            return cls._process_next_chunk_atomic(**kwargs)
        except Http404:
            raise
        except ContributionExpired:
            raise
        except PermissionDenied:
            cls._record_terminal_failure(
                token=kwargs["token"],
                user=kwargs["user"],
                tenant_id=kwargs["tenant_id"],
                failure_code="AUTHORIZATION_CHANGED",
            )
            raise
        except ContributionConflict as exc:
            if "Another interrupted question import" not in str(exc):
                code = (
                    "QUOTA_CHANGED"
                    if "quota" in str(exc).lower()
                    else "STALE_CONTRIBUTION"
                )
                cls._record_terminal_failure(
                    token=kwargs["token"],
                    user=kwargs["user"],
                    tenant_id=kwargs["tenant_id"],
                    failure_code=code,
                )
            raise
        except ValidationError:
            cls._record_terminal_failure(
                token=kwargs["token"],
                user=kwargs["user"],
                tenant_id=kwargs["tenant_id"],
                failure_code="INVALID_IMPORT_STATE",
            )
            raise
        except Exception:
            cls._record_resumable_failure(
                token=kwargs["token"],
                user=kwargs["user"],
                tenant_id=kwargs["tenant_id"],
                failure_code="PROCESSING_INTERRUPTED",
            )
            raise

    @classmethod
    def confirm(
        cls,
        *,
        token,
        expected_file_sha256,
        user,
        tenant_id,
        campus_id,
        request=None,
    ):
        before = cls.owner_batch(token=token, user=user, tenant_id=tenant_id)
        changed = before.status != QuestionImportBatch.Status.CONFIRMED
        if not changed:
            return before, False
        max_chunks = max(1, before.total_rows) + 1
        for _index in range(max_chunks):
            batch, _created = cls.process_next_chunk(
                token=token,
                expected_file_sha256=expected_file_sha256,
                user=user,
                tenant_id=tenant_id,
                campus_id=campus_id,
                request=request,
            )
            if batch.status == QuestionImportBatch.Status.CONFIRMED:
                return batch, True
        raise ValidationError("The import did not reach a terminal state safely.")


class QuestionImportCleanupService:
    @classmethod
    def purge(cls, *, batch_size=200, now=None):
        now = now or timezone.now()
        expired = 0
        rows_purged = 0
        shells_purged = 0
        candidate_ids = list(
            QuestionImportBatch.objects.filter(
                status__in=[QuestionImportBatch.Status.READY, QuestionImportBatch.Status.INVALID],
                expires_at__lte=now,
            )
            .order_by("expires_at", "id")
            .values_list("id", flat=True)[:batch_size]
        )
        for batch_id in candidate_ids:
            with transaction.atomic():
                batch = QuestionImportBatch.objects.select_for_update().filter(
                    pk=batch_id,
                    status__in=[QuestionImportBatch.Status.READY, QuestionImportBatch.Status.INVALID],
                    expires_at__lte=now,
                ).first()
                if batch is None:
                    continue
                deleted, _ = QuestionImportRow.objects.filter(batch=batch).delete()
                rows_purged += deleted
                batch.status = QuestionImportBatch.Status.EXPIRED
                batch.payload_purged_at = now
                batch.save(update_fields=["status", "payload_purged_at", "updated_at"])
                expired += 1
        remaining = max(batch_size - expired, 0)
        if remaining:
            shell_ids = list(
                QuestionImportBatch.objects.filter(
                    status__in=[
                        QuestionImportBatch.Status.FAILED,
                        QuestionImportBatch.Status.CONFIRMED,
                        QuestionImportBatch.Status.EXPIRED,
                    ],
                    created_at__lte=now - SHELL_RETENTION,
                )
                .order_by("created_at", "id")
                .values_list("id", flat=True)[:remaining]
            )
            for batch_id in shell_ids:
                with transaction.atomic():
                    batch = QuestionImportBatch.objects.select_for_update().filter(
                        pk=batch_id,
                        status__in=[
                            QuestionImportBatch.Status.FAILED,
                            QuestionImportBatch.Status.CONFIRMED,
                            QuestionImportBatch.Status.EXPIRED,
                        ],
                        created_at__lte=now - SHELL_RETENTION,
                    ).first()
                    if batch is None:
                        continue
                    batch.delete()
                    shells_purged += 1
        return {
            "expired_batches": expired,
            "rows_purged": rows_purged,
            "shells_purged": shells_purged,
        }
