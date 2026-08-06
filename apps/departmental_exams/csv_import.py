from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass, field
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.utils import timezone

from apps.core.services.audit import AuditService

from .contribution_authorization import (
    ContributionAuthorizationService,
    ContributionConflict,
    ContributionExpired,
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
PREVIEW_LIFETIME = timedelta(minutes=30)
SHELL_RETENTION = timedelta(days=30)


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
            row = ParsedImportRow(row_number=offset)
            try:
                row.payload = QuestionPayloadService.validate(raw_payload)
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
                row.payload = {
                    field_name: QuestionPayloadService.normalize_text(raw_payload.get(field_name))
                    for field_name in CSV_HEADERS
                }
            rows.append(row)
        return ParsedImport(
            raw_sha256=raw_hash,
            filename_sha256=filename_hash,
            rows=rows,
        )


class QuestionCSVImportService:
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
            contribution=contribution,
            configuration=configuration,
            request_tenant_id=tenant_id,
            request_campus_id=campus_id,
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
            QuestionPayloadService.question_fingerprint(value)
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
    @transaction.atomic
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
        if batch.status == QuestionImportBatch.Status.CONFIRMED:
            return batch, False
        if timezone.now() >= batch.expires_at:
            raise ContributionExpired("This confidential preview has expired. Upload the CSV again.")
        if batch.status != QuestionImportBatch.Status.READY or batch.error_count:
            raise ValidationError("Only a Ready preview without blocking errors can be confirmed.")
        ContributionAuthorizationService.require_mutable_locked(
            contribution=contribution,
            configuration=configuration,
            request_tenant_id=tenant_id,
            request_campus_id=campus_id,
        )
        existing_questions = list(
            Question.objects.select_for_update()
            .filter(contribution=contribution)
            .order_by("pk")
        )
        ContributionAuthorizationService.require_add_capacity(
            contribution=contribution,
            question_count=len(existing_questions),
        )
        if contribution.revision != batch.contribution_revision_snapshot:
            raise ContributionConflict(
                "This preview is stale because the contribution changed. Upload the CSV again."
            )
        rows = list(
            QuestionImportRow.objects.select_for_update()
            .filter(batch=batch)
            .order_by("row_number")
        )
        if (
            len(rows) != batch.total_rows
            or any(row.errors for row in rows)
            or len(existing_questions) + len(rows) > contribution.quota_snapshot
        ):
            raise ValidationError("The preview is no longer valid for atomic confirmation.")
        cleaned_rows = [QuestionPayloadService.validate(row.payload) for row in rows]
        start_position = len(existing_questions) + 1
        questions = [
            Question(
                contribution=contribution,
                position=start_position + offset,
                revision=1,
                entry_method=Question.EntryMethod.CSV,
                import_batch=batch,
                **payload,
            )
            for offset, payload in enumerate(cleaned_rows)
        ]
        Question.objects.bulk_create(questions, batch_size=CSV_MAX_ROWS)
        revision_before = contribution.revision
        contribution.revision += 1
        contribution.save(update_fields=["revision", "updated_at"])
        now = timezone.now()
        batch.status = QuestionImportBatch.Status.CONFIRMED
        batch.confirming_user = user
        batch.confirmed_at = now
        batch.payload_purged_at = now
        batch.save(update_fields=[
            "status",
            "confirming_user",
            "confirmed_at",
            "payload_purged_at",
            "updated_at",
        ])
        QuestionImportRow.objects.filter(batch=batch).delete()
        difficulty_counts = {}
        for payload in cleaned_rows:
            difficulty = payload["difficulty"]
            difficulty_counts[difficulty] = difficulty_counts.get(difficulty, 0) + 1
        AuditService.log_event(
            action="DE_EXAM_QUESTION_CSV_IMPORTED",
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
                "token_sha256": hashlib.sha256(str(batch.token).encode("ascii")).hexdigest(),
                "filename_sha256": batch.filename_sha256,
                "row_count": len(cleaned_rows),
                "warning_count": batch.warning_count,
                "quota": contribution.quota_snapshot,
                "resulting_count": len(existing_questions) + len(cleaned_rows),
                "revision_before": revision_before,
                "revision_after": contribution.revision,
                "difficulty_counts": difficulty_counts,
            },
            request=request,
        )
        return batch, True


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
                    status__in=[QuestionImportBatch.Status.CONFIRMED, QuestionImportBatch.Status.EXPIRED],
                    created_at__lte=now - SHELL_RETENTION,
                )
                .order_by("created_at", "id")
                .values_list("id", flat=True)[:remaining]
            )
            for batch_id in shell_ids:
                with transaction.atomic():
                    batch = QuestionImportBatch.objects.select_for_update().filter(
                        pk=batch_id,
                        status__in=[QuestionImportBatch.Status.CONFIRMED, QuestionImportBatch.Status.EXPIRED],
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
