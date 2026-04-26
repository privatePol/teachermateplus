from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from django.core.exceptions import ValidationError
from PIL import Image


@dataclass(frozen=True)
class UploadValidationResult:
    original_filename: str
    content_type: str
    file_size_bytes: int
    extension: str


class UploadValidationService:
    CORRECTION_ATTACHMENT_MAX_BYTES = 5 * 1024 * 1024
    IMPORT_CSV_MAX_BYTES = 10 * 1024 * 1024
    CORRECTION_ATTACHMENT_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}

    @staticmethod
    def _extension(uploaded_file) -> str:
        return Path(getattr(uploaded_file, "name", "") or "").suffix.lower()

    @staticmethod
    def _content_type(uploaded_file) -> str:
        supplied = (getattr(uploaded_file, "content_type", "") or "").strip().lower()
        if supplied:
            return supplied
        guessed, _encoding = mimetypes.guess_type(getattr(uploaded_file, "name", "") or "")
        return (guessed or "application/octet-stream").lower()

    @staticmethod
    def _size(uploaded_file) -> int:
        size = getattr(uploaded_file, "size", None)
        if size is not None:
            return int(size)
        position = uploaded_file.tell() if hasattr(uploaded_file, "tell") else None
        data = uploaded_file.read()
        if position is not None and hasattr(uploaded_file, "seek"):
            uploaded_file.seek(position)
        return len(data or b"")

    @staticmethod
    def _read_sample(uploaded_file, size: int = 4096) -> bytes:
        position = uploaded_file.tell() if hasattr(uploaded_file, "tell") else None
        sample = uploaded_file.read(size) or b""
        if position is not None and hasattr(uploaded_file, "seek"):
            uploaded_file.seek(position)
        return sample

    @staticmethod
    def _validate_image(uploaded_file) -> None:
        position = uploaded_file.tell() if hasattr(uploaded_file, "tell") else None
        raw = uploaded_file.read()
        if position is not None and hasattr(uploaded_file, "seek"):
            uploaded_file.seek(position)
        try:
            image = Image.open(BytesIO(raw))
            image.verify()
        except Exception as exc:
            raise ValidationError("Uploaded image is not valid.") from exc

    @classmethod
    def validate_correction_attachment(cls, uploaded_file) -> UploadValidationResult:
        if not uploaded_file:
            raise ValidationError("Upload an attachment first.")

        original_filename = str(getattr(uploaded_file, "name", "") or "").strip()
        extension = cls._extension(uploaded_file)
        if extension not in cls.CORRECTION_ATTACHMENT_EXTENSIONS:
            raise ValidationError("Correction attachments must be PDF, PNG, JPG, or JPEG files.")

        size = cls._size(uploaded_file)
        if size <= 0:
            raise ValidationError("Attachment file is empty.")
        if size > cls.CORRECTION_ATTACHMENT_MAX_BYTES:
            raise ValidationError("Correction attachments must be 5 MB or smaller.")

        sample = cls._read_sample(uploaded_file)
        if extension == ".pdf":
            if not sample.startswith(b"%PDF"):
                raise ValidationError("PDF attachment does not appear to be a valid PDF file.")
            content_type = "application/pdf"
        else:
            cls._validate_image(uploaded_file)
            content_type = "image/jpeg" if extension in {".jpg", ".jpeg"} else "image/png"

        return UploadValidationResult(
            original_filename=original_filename,
            content_type=content_type,
            file_size_bytes=size,
            extension=extension,
        )

    @classmethod
    def validate_import_csv(cls, uploaded_file) -> UploadValidationResult:
        if not uploaded_file:
            raise ValidationError("Upload a CSV file first.")
        original_filename = str(getattr(uploaded_file, "name", "") or "").strip()
        extension = cls._extension(uploaded_file)
        if extension != ".csv":
            raise ValidationError("Only .csv files are allowed.")
        size = cls._size(uploaded_file)
        if size <= 0:
            raise ValidationError("CSV file is empty.")
        if size > cls.IMPORT_CSV_MAX_BYTES:
            raise ValidationError("CSV file must be 10 MB or smaller.")
        sample = cls._read_sample(uploaded_file)
        if b"\x00" in sample:
            raise ValidationError("CSV file appears to contain binary content.")
        content_type = cls._content_type(uploaded_file)
        return UploadValidationResult(
            original_filename=original_filename,
            content_type=content_type,
            file_size_bytes=size,
            extension=extension,
        )
