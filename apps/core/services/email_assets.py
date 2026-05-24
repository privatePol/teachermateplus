from __future__ import annotations

from email.mime.image import MIMEImage
from pathlib import Path

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

EMAIL_SUBJECT_PREFIX = "NCBA | EduGradePlus: "


def format_email_subject(message: str) -> str:
    text = str(message or "").strip()
    legacy_prefixes = [
        EMAIL_SUBJECT_PREFIX,
        "NCBA-EduGrade+:",
        "NCBA EduGrade+",
        "EduGrade+",
        "EduGradePlus",
    ]
    for prefix in legacy_prefixes:
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix):].strip()
            if text.startswith(":"):
                text = text[1:].strip()
            break
    return f"{EMAIL_SUBJECT_PREFIX}{text or 'Notification'}"


def _resolve_logo_path(*, filename: str, configured_path: str = "") -> Path | None:
    paths: list[Path] = []
    if configured_path:
        paths.append(Path(configured_path))
    paths.extend(
        [
            Path(settings.MEDIA_ROOT) / "logos" / filename,
            Path(settings.BASE_DIR) / "media" / "logos" / filename,
        ]
    )
    for path in paths:
        if path.exists() and path.is_file():
            return path
    return None


def build_email_logo_context(
    *,
    filename: str,
    cid: str,
    external_url: str = "",
    configured_path: str = "",
) -> dict[str, str]:
    url = (external_url or "").strip()
    if url:
        return {"logo_url": url, "logo_src": url, "email_logo_src": url}
    if _resolve_logo_path(filename=filename, configured_path=configured_path):
        src = f"cid:{cid}"
        return {"logo_url": src, "logo_src": src, "email_logo_src": src}
    return {"logo_url": "", "logo_src": "", "email_logo_src": ""}


def attach_inline_email_logo(
    message: EmailMultiAlternatives,
    *,
    filename: str,
    cid: str,
    configured_path: str = "",
) -> bool:
    logo_path = _resolve_logo_path(filename=filename, configured_path=configured_path)
    if logo_path is None:
        return False
    try:
        logo_part = MIMEImage(logo_path.read_bytes())
        logo_part.add_header("Content-ID", f"<{cid}>")
        logo_part.add_header("Content-Disposition", "inline", filename=logo_path.name)
        message.attach(logo_part)
        return True
    except Exception:
        return False


def attach_logo_for_src(
    message: EmailMultiAlternatives,
    *,
    src: str,
    filename: str,
    cid: str,
    configured_path: str = "",
) -> bool:
    if (src or "").strip() != f"cid:{cid}":
        return False
    return attach_inline_email_logo(
        message,
        filename=filename,
        cid=cid,
        configured_path=configured_path,
    )
