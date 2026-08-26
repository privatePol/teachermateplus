from __future__ import annotations

import hashlib
import io
import re
import zipfile
from pathlib import PurePosixPath
from xml.etree.ElementTree import ParseError

from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404
from django.utils import timezone

from apps.core.services.features import FeatureSettingsService

from .contribution_authorization import (
    ContributionAuthorizationService,
    ContributionConflict,
    ContributionExpired,
)
from .contribution_services import QuestionPayloadService, Stage5LockService
from .csv_import import (
    CSV_HEADERS,
    CSV_MAX_ROWS,
    CSV_SANITIZATION_WARNING,
    PREVIEW_LIFETIME,
    ParsedImport,
    ParsedImportRow,
    QuestionCSVImportService,
    sanitize_csv_question_payload,
    sanitize_csv_question_text,
)
from .models import Question, QuestionImportBatch, QuestionImportRow


DOCX_MAX_BYTES = 2 * 1024 * 1024
DOCX_MAX_MEMBERS = 256
DOCX_MAX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024
DOCX_MAX_XML_BYTES = 5 * 1024 * 1024
DOCX_MAX_COMPRESSION_RATIO = 100
DOCX_MAIN_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument."
    "wordprocessingml.document.main+xml"
)

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"

QUESTION_RE = re.compile(r"^\s*(\d{1,3})[.)] (.+)$", re.DOTALL)
CHOICE_RE = re.compile(r"^\s*([A-Da-d])[.)] (.+)$", re.DOTALL)
ANSWER_RE = re.compile(r"^\s*Answer\s*:\s*(.*)$", re.IGNORECASE | re.DOTALL)
DIFFICULTY_RE = re.compile(
    r"^\s*Difficulty\s*:\s*(.*)$", re.IGNORECASE | re.DOTALL
)
EXISTING_DUPLICATE_MESSAGE = "This question resembles one already saved in your contribution."
STAGED_DUPLICATE_MESSAGE = "This question resembles another row in this Word preview."
DUPLICATE_MESSAGES = {EXISTING_DUPLICATE_MESSAGE, STAGED_DUPLICATE_MESSAGE}

UNSUPPORTED_SEMANTIC_INLINE_TAGS = {
    f"{W}sym": "Word symbol-font characters",
    f"{W}ruby": "Word phonetic or ruby annotations",
    f"{W}fldSimple": "Word fields",
    f"{W}fldChar": "Word fields",
    f"{W}instrText": "Word fields",
    f"{W}delInstrText": "Word fields",
    f"{W}dayShort": "Generated date fields",
    f"{W}dayLong": "Generated date fields",
    f"{W}monthShort": "Generated date fields",
    f"{W}monthLong": "Generated date fields",
    f"{W}yearShort": "Generated date fields",
    f"{W}yearLong": "Generated date fields",
    f"{W}pgNum": "Generated page-number fields",
    f"{W}annotationRef": "Generated annotation references",
    f"{W}footnoteRef": "Generated footnote references",
    f"{W}endnoteRef": "Generated endnote references",
    f"{W}separator": "Generated separator marks",
    f"{W}continuationSeparator": "Generated separator marks",
    f"{W}contentPart": "Linked semantic content",
    f"{W}subDoc": "Embedded subdocuments",
    f"{W}dir": "Bidirectional run containers",
    f"{W}bdo": "Bidirectional override containers",
    f"{MC}AlternateContent": "Alternate-content branches",
}

RECOGNIZED_RUN_CHILDREN = {
    f"{W}rPr",
    f"{W}t",
    f"{W}br",
    f"{W}cr",
    f"{W}tab",
    f"{W}noBreakHyphen",
    f"{W}softHyphen",
    f"{W}lastRenderedPageBreak",
}


class DOCXPackageError(Exception):
    pass


class QuestionDOCXParser:
    @staticmethod
    def _error(message, *, field="file"):
        return ParsedImportRow(
            row_number=1,
            errors=[{"field": field, "message": message}],
        )

    @classmethod
    def _failure(cls, *, raw_hash, filename_hash, message):
        return ParsedImport(
            raw_sha256=raw_hash,
            filename_sha256=filename_hash,
            rows=[cls._error(message)],
        )

    @staticmethod
    def _safe_xml(raw, *, member_name):
        try:
            return DefusedElementTree.fromstring(raw)
        except (DefusedXmlException, ValueError, ParseError) as exc:
            raise DOCXPackageError(
                f"The Word package contains unsafe or malformed XML in {member_name}."
            ) from exc

    @staticmethod
    def _read_bounded(package, member_name, *, limit=DOCX_MAX_XML_BYTES):
        try:
            with package.open(member_name, "r") as member:
                raw = member.read(limit + 1)
        except (KeyError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise DOCXPackageError(
                f"The Word package member {member_name} could not be read safely."
            ) from exc
        if len(raw) > limit:
            raise DOCXPackageError(f"The Word package member {member_name} exceeds its safety limit.")
        return raw

    @staticmethod
    def _validate_member_name(name):
        if not name or "\\" in name or name.startswith(("/", "\\")):
            raise DOCXPackageError("The Word package contains an unsafe member path.")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or any(":" in part for part in path.parts):
            raise DOCXPackageError("The Word package contains an unsafe member path.")

    @classmethod
    def _validate_relationships(cls, package, names):
        office_document_targets = []
        for name in sorted(item for item in names if item.lower().endswith(".rels")):
            root = cls._safe_xml(cls._read_bounded(package, name), member_name=name)
            for relationship in root.findall(f"{PKG_REL}Relationship"):
                if (relationship.get("TargetMode") or "").upper() == "EXTERNAL":
                    raise DOCXPackageError("External relationships are not supported.")
                rel_type = (relationship.get("Type") or "").lower()
                target = relationship.get("Target") or ""
                if (
                    not target
                    or "\\" in target
                    or target.startswith("/")
                    or ".." in PurePosixPath(target.split("#", 1)[0]).parts
                    or "://" in target
                ):
                    raise DOCXPackageError("The Word package contains an unsafe relationship target.")
                if name == "_rels/.rels" and rel_type.endswith("/officedocument"):
                    office_document_targets.append(target)
                if any(
                    marker in rel_type
                    for marker in (
                        "image", "oleobject", "package", "activex", "afchunk", "chart",
                        "header", "footer", "footnotes", "endnotes", "comments",
                    )
                ):
                    raise DOCXPackageError(
                        "Images/charts, embedded packages, ActiveX, OLE, altChunk, headers/footers, "
                        "footnotes/endnotes, and comments are not supported."
                    )
        if office_document_targets != ["word/document.xml"]:
            raise DOCXPackageError(
                "The Word package does not identify exactly one standard main document part."
            )

    @classmethod
    def _validate_content_types(cls, package):
        root = cls._safe_xml(
            cls._read_bounded(package, "[Content_Types].xml"),
            member_name="[Content_Types].xml",
        )
        main_types = []
        for declaration in list(root):
            content_type = (declaration.get("ContentType") or "").lower()
            part_name = (declaration.get("PartName") or "").lower()
            if declaration.tag == f"{CT}Override" and part_name == "/word/document.xml":
                main_types.append(content_type)
            if any(marker in content_type for marker in ("macroenabled", "vba", "activex", "oleobject")):
                raise DOCXPackageError("Macro-enabled, VBA, ActiveX, and OLE Word packages are not supported.")
        if main_types != [DOCX_MAIN_CONTENT_TYPE]:
            raise DOCXPackageError("The upload is not a standard macro-free Word .docx document.")

    @classmethod
    def _open_validated_package(cls, raw):
        try:
            package = zipfile.ZipFile(io.BytesIO(raw))
            members = package.infolist()
        except (zipfile.BadZipFile, OSError) as exc:
            raise DOCXPackageError("The upload is not a valid Word .docx ZIP/OPC package.") from exc
        if len(members) > DOCX_MAX_MEMBERS:
            package.close()
            raise DOCXPackageError("The Word package contains too many ZIP members.")
        names = set()
        folded_names = set()
        total_size = 0
        for member in members:
            cls._validate_member_name(member.filename)
            folded = member.filename.casefold()
            if member.filename in names or folded in folded_names:
                package.close()
                raise DOCXPackageError("The Word package contains duplicate ZIP member names.")
            names.add(member.filename)
            folded_names.add(folded)
            if member.flag_bits & 0x1:
                package.close()
                raise DOCXPackageError("Encrypted ZIP members are not supported.")
            if member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                package.close()
                raise DOCXPackageError("The Word package uses unsupported ZIP compression.")
            total_size += member.file_size
            if total_size > DOCX_MAX_UNCOMPRESSED_BYTES:
                package.close()
                raise DOCXPackageError("The Word package expands beyond the 20 MB safety limit.")
            if member.filename.lower().endswith((".xml", ".rels")) and member.file_size > DOCX_MAX_XML_BYTES:
                package.close()
                raise DOCXPackageError("An XML member exceeds the 5 MB safety limit.")
            if member.file_size and (
                member.compress_size == 0
                or member.file_size / member.compress_size > DOCX_MAX_COMPRESSION_RATIO
            ):
                package.close()
                raise DOCXPackageError("The Word package has a suspicious compression ratio.")
        required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
        if not required.issubset(names):
            package.close()
            raise DOCXPackageError("The upload is missing required Word document parts.")
        lowered = {name.lower() for name in names}
        if any(
            marker in name
            for name in lowered
            for marker in (
                "vbaproject", "activex", "embeddings/", "word/media/", "word/charts/",
                "word/header", "word/footer", "word/footnotes", "word/endnotes",
                "word/comments",
            )
        ):
            package.close()
            raise DOCXPackageError(
                "VBA, ActiveX, embedded packages, images/charts, headers/footers, "
                "footnotes/endnotes, and comments are not supported."
            )
        return package, names

    @staticmethod
    def _paragraph_text(paragraph):
        for run in paragraph.iter(f"{W}r"):
            for child in run:
                if child.tag in RECOGNIZED_RUN_CHILDREN:
                    continue
                raise DOCXPackageError(
                    "The Word document contains an unsupported semantic inline element; "
                    "replace it with ordinary Unicode text."
                )
        pieces = []
        for node in paragraph.iter():
            if node.tag == f"{W}t":
                pieces.append(node.text or "")
            elif node.tag in {f"{W}br", f"{W}cr"}:
                pieces.append("\n")
            elif node.tag == f"{W}noBreakHyphen":
                pieces.append("\u2011")
            elif node.tag == f"{W}softHyphen":
                pieces.append("\u00ad")
            elif node.tag == f"{W}tab":
                raise DOCXPackageError("Word tab characters are not supported; replace them with ordinary spaces.")
        return "".join(pieces)

    @classmethod
    def _document_paragraphs(cls, package):
        root = cls._safe_xml(
            cls._read_bounded(package, "word/document.xml"),
            member_name="word/document.xml",
        )
        rejected_tags = {
            f"{W}tbl": "Word tables",
            f"{W}drawing": "Images or drawings",
            f"{W}pict": "Images, drawings, or text boxes",
            f"{W}object": "Embedded objects",
            f"{W}txbxContent": "Text boxes",
            f"{W}altChunk": "altChunk content",
            f"{M}oMath": "Word equations",
            f"{M}oMathPara": "Word equations",
            f"{W}footnoteReference": "Footnotes",
            f"{W}endnoteReference": "Endnotes",
            f"{W}commentReference": "Comments",
            f"{W}ins": "Tracked changes",
            f"{W}del": "Tracked changes",
            f"{W}moveFrom": "Tracked changes",
            f"{W}moveTo": "Tracked changes",
            **UNSUPPORTED_SEMANTIC_INLINE_TAGS,
        }
        for node in root.iter():
            if node.tag in rejected_tags:
                raise DOCXPackageError(f"{rejected_tags[node.tag]} are not supported in Word import V1.")
            if node.tag == f"{W}numPr":
                raise DOCXPackageError(
                    "Automatically numbered questions or choices are not supported; type the numbers and A-D labels as text."
                )
        body = root.find(f"{W}body")
        if body is None:
            raise DOCXPackageError("The Word document has no readable body.")
        if any(child.tag not in {f"{W}p", f"{W}sectPr"} for child in body):
            raise DOCXPackageError(
                "The Word body contains an unsupported nested structure; only ordinary paragraphs are supported."
            )
        return [
            cls._paragraph_text(child)
            for child in body
            if child.tag == f"{W}p"
        ]

    @classmethod
    def _rows_from_paragraphs(cls, paragraphs):
        rows = []
        current = None
        active_field = None
        pending_blank_paragraphs = []
        expected_question_number = 1

        def finish():
            nonlocal current, active_field, pending_blank_paragraphs
            if current is None:
                return
            raw_payload = {field: current["payload"].get(field, "") for field in CSV_HEADERS}
            sanitized, changed = sanitize_csv_question_payload(raw_payload)
            row = ParsedImportRow(row_number=len(rows) + 2)
            row.errors.extend(current["errors"])
            if changed:
                row.warnings.append({"field": "row", "message": CSV_SANITIZATION_WARNING})
            try:
                row.payload = QuestionPayloadService.validate(sanitized)
                row.fingerprint = QuestionPayloadService.question_fingerprint(row.payload["question_text"])
            except ValidationError as exc:
                if hasattr(exc, "message_dict"):
                    for field, messages in exc.message_dict.items():
                        row.errors.extend({"field": field, "message": str(message)} for message in messages)
                else:
                    row.errors.extend({"field": "row", "message": str(message)} for message in exc.messages)
                unsafe = any(
                    QuestionPayloadService.has_unsupported_characters(sanitized.get(field))
                    for field in QuestionPayloadService.TEXT_FIELDS
                )
                row.payload = {} if unsafe else {
                    field: QuestionPayloadService.normalize_text(sanitized.get(field))
                    for field in CSV_HEADERS
                }
            rows.append(row)
            current = None
            active_field = None
            pending_blank_paragraphs = []

        for paragraph in paragraphs:
            if not paragraph.strip():
                if current is not None and active_field:
                    pending_blank_paragraphs.append(paragraph)
                continue
            if paragraph.strip().lower().startswith("answer key"):
                raise DOCXPackageError("Answer keys located at the end of the document are not supported.")
            question_match = QUESTION_RE.match(paragraph)
            if question_match:
                question_number = int(question_match.group(1))
                if question_number != expected_question_number:
                    raise DOCXPackageError(
                        "Typed question numbers must begin at 1 and remain sequential; numbering was not stripped."
                    )
                finish()
                current = {"payload": {"question_text": question_match.group(2)}, "errors": []}
                active_field = "question_text"
                pending_blank_paragraphs = []
                expected_question_number += 1
                continue
            if current is None:
                raise DOCXPackageError(
                    "Each question must begin with a typed number such as '1.' followed by the question text."
                )
            choice_match = CHOICE_RE.match(paragraph)
            if choice_match:
                field = f"choice_{choice_match.group(1).lower()}"
                if field in current["payload"]:
                    current["errors"].append({"field": field, "message": "This choice label appears more than once."})
                current["payload"][field] = choice_match.group(2)
                active_field = field
                pending_blank_paragraphs = []
                continue
            answer_match = ANSWER_RE.match(paragraph)
            if answer_match:
                if "correct_answer" in current["payload"]:
                    current["errors"].append({"field": "correct_answer", "message": "Answer appears more than once."})
                current["payload"]["correct_answer"] = answer_match.group(1)
                active_field = None
                pending_blank_paragraphs = []
                continue
            difficulty_match = DIFFICULTY_RE.match(paragraph)
            if difficulty_match:
                if "difficulty" in current["payload"]:
                    current["errors"].append({"field": "difficulty", "message": "Difficulty appears more than once."})
                current["payload"]["difficulty"] = difficulty_match.group(1)
                active_field = None
                pending_blank_paragraphs = []
                continue
            if active_field:
                current["payload"][active_field] += "\n" + "\n".join(
                    [*pending_blank_paragraphs, paragraph]
                )
                pending_blank_paragraphs = []
            else:
                current["errors"].append({
                    "field": "row",
                    "message": "An unrecognized paragraph was preserved for review; rewrite the staged fields before import.",
                })
                current["payload"]["question_text"] += "\n" + paragraph
        finish()
        if not rows:
            raise DOCXPackageError("No supported paragraph-based questions were detected.")
        if len(rows) > CSV_MAX_ROWS:
            raise DOCXPackageError("A Word file may contain at most 200 detected questions.")
        return rows

    @classmethod
    def parse(cls, uploaded_file):
        filename = uploaded_file.name or ""
        filename_hash = hashlib.sha256(filename.encode("utf-8")).hexdigest()
        empty_hash = hashlib.sha256(b"").hexdigest()
        if not filename.lower().endswith(".docx"):
            return cls._failure(raw_hash=empty_hash, filename_hash=filename_hash, message="Upload a standard .docx file. Legacy .doc, macro-enabled, template, RTF, ODT, and PDF files are not supported.")
        if uploaded_file.size > DOCX_MAX_BYTES:
            return cls._failure(raw_hash=empty_hash, filename_hash=filename_hash, message="Word .docx files may not exceed 2 MB.")
        raw = uploaded_file.read()
        raw_hash = hashlib.sha256(raw).hexdigest()
        if len(raw) > DOCX_MAX_BYTES:
            return cls._failure(raw_hash=raw_hash, filename_hash=filename_hash, message="Word .docx files may not exceed 2 MB.")
        try:
            package, names = cls._open_validated_package(raw)
            with package:
                cls._validate_content_types(package)
                cls._validate_relationships(package, names)
                rows = cls._rows_from_paragraphs(cls._document_paragraphs(package))
        except (DOCXPackageError, RuntimeError, zipfile.BadZipFile) as exc:
            return cls._failure(raw_hash=raw_hash, filename_hash=filename_hash, message=str(exc) or "The Word package could not be parsed safely.")
        return ParsedImport(raw_sha256=raw_hash, filename_sha256=filename_hash, rows=rows)


class QuestionDOCXImportService(QuestionCSVImportService):
    @staticmethod
    def require_feature(*, tenant_id):
        if not FeatureSettingsService.is_departmental_exam_docx_import_enabled(
            tenant_id=tenant_id, default=False
        ):
            raise PermissionDenied("Word question import is disabled for this tenant.")

    @classmethod
    @transaction.atomic
    def create_preview(
        cls, *, contribution_id, uploaded_file, user, tenant_id, campus_id,
        expected_contribution_revision,
    ):
        _cycle, _course, configuration, contribution = Stage5LockService.lock_contribution(
            contribution_id=contribution_id, user=user, tenant_id=tenant_id
        )
        ContributionAuthorizationService.require_mutable_locked(
            contribution=contribution,
            configuration=configuration,
            request_tenant_id=tenant_id,
            request_campus_id=campus_id,
        )
        cls.require_feature(tenant_id=tenant_id)
        ContributionAuthorizationService.require_no_active_import(contribution=contribution)
        ContributionAuthorizationService.require_revision(
            contribution=contribution, expected_revision=expected_contribution_revision
        )
        existing_questions = list(Question.objects.filter(contribution=contribution).order_by("pk"))
        ContributionAuthorizationService.require_add_capacity(
            contribution=contribution, question_count=len(existing_questions)
        )
        parsed = QuestionDOCXParser.parse(uploaded_file)
        data_rows = parsed.data_rows
        remaining = contribution.quota_snapshot - len(existing_questions)
        if len(data_rows) > remaining:
            parsed.rows.insert(0, QuestionDOCXParser._error(
                f"The Word file has {len(data_rows)} questions but only {remaining} quota slots remain.",
                field="quota",
            ))
        existing_fingerprints = {
            QuestionPayloadService.question_fingerprint(sanitize_csv_question_text(value))
            for value in Question.objects.filter(contribution__faculty_user=user).values_list("question_text", flat=True)
        }
        seen = set()
        for row in data_rows:
            if row.errors or not row.fingerprint:
                continue
            if row.fingerprint in existing_fingerprints:
                row.warnings.append({"field": "question_text", "message": EXISTING_DUPLICATE_MESSAGE})
            if row.fingerprint in seen:
                row.warnings.append({"field": "question_text", "message": STAGED_DUPLICATE_MESSAGE})
            seen.add(row.fingerprint)
        error_count = parsed.error_count
        warning_count = parsed.warning_count
        valid_rows = sum(1 for row in data_rows if not row.errors)
        batch = QuestionImportBatch.objects.create(
            tenant_id=tenant_id,
            contribution=contribution,
            uploading_user=user,
            status=(QuestionImportBatch.Status.READY if error_count == 0 and valid_rows else QuestionImportBatch.Status.INVALID),
            source_format=QuestionImportBatch.SourceFormat.DOCX,
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
        QuestionImportRow.objects.bulk_create([
            QuestionImportRow(
                batch=batch, row_number=row.row_number, payload=row.payload,
                errors=row.errors, warnings=row.warnings, fingerprint=row.fingerprint,
            ) for row in parsed.rows
        ], batch_size=CSV_MAX_ROWS)
        return batch

    @classmethod
    @transaction.atomic
    def update_staged_row(
        cls, *, token, row_number, payload, user, tenant_id, campus_id,
        expected_contribution_revision,
    ):
        identity = cls._owner_batch_identity(token=token, user=user, tenant_id=tenant_id)
        _cycle, _course, configuration, contribution = Stage5LockService.lock_contribution(
            contribution_id=identity["contribution_id"], user=user, tenant_id=tenant_id
        )
        batch = QuestionImportBatch.objects.select_for_update().filter(
            pk=identity["id"], contribution=contribution,
            source_format=QuestionImportBatch.SourceFormat.DOCX,
        ).first()
        if batch is None:
            raise Http404
        cls.require_feature(tenant_id=tenant_id)
        ContributionAuthorizationService.require_mutable_locked(
            contribution=contribution, configuration=configuration,
            request_tenant_id=tenant_id, request_campus_id=campus_id,
        )
        ContributionAuthorizationService.require_no_active_import(contribution=contribution)
        ContributionAuthorizationService.require_revision(
            contribution=contribution, expected_revision=expected_contribution_revision
        )
        if contribution.revision != batch.contribution_revision_snapshot:
            raise ContributionConflict("This Word preview is stale because the contribution changed.")
        if batch.status not in {batch.Status.INVALID, batch.Status.READY}:
            raise ValidationError("Only a staged Word preview can be edited.")
        if timezone.now() >= batch.expires_at:
            raise ContributionExpired("This confidential Word preview has expired.")
        row = QuestionImportRow.objects.select_for_update().filter(
            batch=batch, row_number=row_number
        ).first()
        if row is None:
            raise Http404
        sanitized, changed = sanitize_csv_question_payload(payload)
        errors = []
        fingerprint = ""
        try:
            cleaned = QuestionPayloadService.validate(sanitized)
            fingerprint = QuestionPayloadService.question_fingerprint(cleaned["question_text"])
        except ValidationError as exc:
            cleaned = sanitized
            if hasattr(exc, "message_dict"):
                for field, messages in exc.message_dict.items():
                    errors.extend({"field": field, "message": str(message)} for message in messages)
            else:
                errors.extend({"field": "row", "message": str(message)} for message in exc.messages)
            if any(QuestionPayloadService.has_unsupported_characters(sanitized.get(field)) for field in QuestionPayloadService.TEXT_FIELDS):
                cleaned = {}
        row.payload = cleaned
        row.errors = errors
        row.warnings = ([{"field": "row", "message": CSV_SANITIZATION_WARNING}] if changed else [])
        row.fingerprint = fingerprint
        row.save(update_fields=["payload", "errors", "warnings", "fingerprint", "updated_at"])

        rows = list(QuestionImportRow.objects.select_for_update().filter(batch=batch).order_by("row_number"))
        existing = {
            QuestionPayloadService.question_fingerprint(sanitize_csv_question_text(value))
            for value in Question.objects.filter(contribution__faculty_user=user).values_list("question_text", flat=True)
        }
        seen = set()
        for staged in rows:
            warnings = [item for item in staged.warnings if item.get("message") not in DUPLICATE_MESSAGES]
            if not staged.errors and staged.fingerprint:
                if staged.fingerprint in existing:
                    warnings.append({"field": "question_text", "message": EXISTING_DUPLICATE_MESSAGE})
                if staged.fingerprint in seen:
                    warnings.append({"field": "question_text", "message": STAGED_DUPLICATE_MESSAGE})
                seen.add(staged.fingerprint)
            if warnings != staged.warnings:
                staged.warnings = warnings
                staged.save(update_fields=["warnings", "updated_at"])
        batch.error_count = sum(len(item.errors) for item in rows)
        batch.warning_count = sum(len(item.warnings) for item in rows)
        batch.valid_rows = sum(1 for item in rows if not item.errors)
        batch.resulting_question_count = contribution.questions.count() + batch.valid_rows
        batch.status = batch.Status.READY if batch.error_count == 0 and batch.valid_rows else batch.Status.INVALID
        batch.save(update_fields=["error_count", "warning_count", "valid_rows", "resulting_question_count", "status", "updated_at"])
        return batch, row
