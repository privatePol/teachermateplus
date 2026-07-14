from __future__ import annotations


def csv_safe(value):
    """Return a spreadsheet-safe CSV cell without changing ordinary values."""
    text = "" if value is None else str(value)
    if text[:1] in {"=", "+", "-", "@"}:
        return f"'{text}"
    return text
