from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from django.utils import timezone


def _randomized_upload_path(prefix: str, filename: str) -> str:
    now = timezone.now()
    suffix = Path(filename or "").suffix.lower()
    if not suffix or len(suffix) > 12:
        suffix = ".bin"
    return f"{prefix}/{now:%Y/%m}/{uuid4().hex}{suffix}"


def correction_attachment_upload_path(instance, filename: str) -> str:
    return _randomized_upload_path("correction_attachments", filename)


def import_source_upload_path(instance, filename: str) -> str:
    return _randomized_upload_path("imports", filename)
