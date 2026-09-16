"""Canonical rich-content boundary for MCQ stems and choices.

The Case sanitizer remains the authoritative implementation for allowed HTML and
Word semantic normalization.  This module applies the deliberately smaller MCQ
budgets and exposes a stable semantic projection for duplicate identity.
"""
from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser

from django.core.exceptions import ValidationError
from django.utils.html import escape
from django.utils.safestring import mark_safe

from .scenario_content import PLAIN_TEXT, RICH_HTML_V1, canonicalize_scenario_content


TEXT_FIELDS = ("question_text", "choice_a", "choice_b", "choice_c", "choice_d")
CHOICE_FIELDS = TEXT_FIELDS[1:]

MAX_RAW_BY_FIELD = {"question_text": 50_000, **{field: 25_000 for field in CHOICE_FIELDS}}
MAX_RAW_BYTES_BY_FIELD = {"question_text": 100_000, **{field: 50_000 for field in CHOICE_FIELDS}}
MAX_CANONICAL_BY_FIELD = {"question_text": 25_000, **{field: 12_000 for field in CHOICE_FIELDS}}
MAX_VISIBLE_BY_FIELD = {"question_text": 5_000, **{field: 1_000 for field in CHOICE_FIELDS}}
MAX_NODES_BY_FIELD = {"question_text": 800, **{field: 400 for field in CHOICE_FIELDS}}
MAX_TABLES_BY_FIELD = {"question_text": 6, **{field: 4 for field in CHOICE_FIELDS}}
MAX_RAW_CHARACTERS = 100_000
MAX_RAW_BYTES = 200_000
MAX_CANONICAL_CHARACTERS = 50_000
MAX_VISIBLE_CHARACTERS = 9_000
MAX_NODES = 2_000
MAX_TABLES = 12
MAX_TABLE_CELLS = 1_200
MAX_DEPTH = 24
MAX_ROWS_PER_TABLE = 40
MAX_COLUMNS_PER_ROW = 12
MAX_SPAN = 12


@dataclass(frozen=True)
class CanonicalQuestionContent:
    html: str
    visible_text: str
    nodes: int
    tables: int
    cells: int


def plain_text_to_rich_html(value: str) -> str:
    """Escape plain text without ever interpreting HTML-looking source."""
    paragraphs = re.split(r"\n\s*\n", (value or "").replace("\r\n", "\n").replace("\r", "\n"))
    return "".join(
        f"<p class=\"tmp-preserve\">{escape(paragraph).replace(chr(10), '<br>')}</p>"
        for paragraph in paragraphs
        if paragraph.strip()
    )


def render_question_content_for_editor(value: str, *, content_format: str, field: str):
    """Return safe editor HTML without promoting stored plain text.

    The browser editor always works with HTML, but a plain question is projected
    only for display.  That projection escapes source text so a literal
    ``<table>`` remains text, and ``QuestionPayloadService`` retains the original
    plain representation when the projected document is submitted unchanged.
    """
    if content_format == PLAIN_TEXT:
        return mark_safe(plain_text_to_rich_html(value or ""))
    if content_format != RICH_HTML_V1:
        raise ValidationError("Unsupported question content format.")
    return mark_safe(canonicalize_question_content(value or "", field=field).html)


def render_question_content(value: str, content_format: str, *, field: str = "question_text"):
    """Render stored question content only after canonical verification.

    A malformed/unknown historical value is displayed as escaped text.  This is
    intentionally more conservative than trusting an immutable snapshot solely
    because it was stored before a later sanitizer hardening.
    """
    if content_format == PLAIN_TEXT:
        return escape(value or "")
    if content_format != RICH_HTML_V1:
        return escape(value or "")
    try:
        canonical = canonicalize_question_content(value or "", field=field).html
    except ValidationError:
        return escape(value or "")
    if canonical != (value or ""):
        return escape(value or "")
    return mark_safe(canonical)


def render_question_choice_content(value: str, content_format: str):
    """Render a choice against the stricter per-choice MCQ budget."""
    return render_question_content(value, content_format, field="choice_a")


class _QuestionLimitInspector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.nodes = self.depth = self.max_depth = self.cells = 0
        self.tables = []
        self.table_stack = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if tag != "br":
            self.depth += 1
            self.max_depth = max(self.max_depth, self.depth)
        if tag in {"p", "h3", "h4", "br", "li", "tr", "td", "th", "caption"}:
            self.text.append("\n")
        if tag == "table":
            if self.table_stack:
                raise ValidationError("Nested tables are not supported in rich question content.")
            context = {"rows": 0, "occupied": None, "column": 0, "rowspan": {}}
            self.tables.append(context)
            self.table_stack.append(context)
        elif tag == "tr" and self.table_stack:
            context = self.table_stack[-1]
            if context["occupied"] is not None:
                raise ValidationError("Rich question content contains malformed table rows.")
            context["rows"] += 1
            context["occupied"] = set(context["rowspan"])
            context["column"] = 0
        elif tag in {"td", "th"} and self.table_stack:
            context = self.table_stack[-1]
            if context["occupied"] is None:
                raise ValidationError("Rich question content contains a table cell outside a row.")
            attributes = dict(attrs)
            try:
                rowspan = int(attributes.get("rowspan", "1"))
                colspan = int(attributes.get("colspan", "1"))
            except (TypeError, ValueError) as exc:
                raise ValidationError("Table spans must be positive whole numbers.") from exc
            if not 1 <= rowspan <= MAX_SPAN or not 1 <= colspan <= MAX_SPAN:
                raise ValidationError(f"Table spans may be from 1 to {MAX_SPAN}.")
            column = context["column"]
            while column in context["occupied"]:
                column += 1
            columns = set(range(column, column + colspan))
            if columns & context["occupied"] or column + colspan > MAX_COLUMNS_PER_ROW:
                raise ValidationError(
                    f"Each table row may contain at most {MAX_COLUMNS_PER_ROW} columns including merged cells."
                )
            context["occupied"].update(columns)
            context["column"] = column + colspan
            if rowspan > 1:
                for item in columns:
                    context["rowspan"][item] = rowspan
            self.cells += 1

    def handle_endtag(self, tag):
        if tag in {"p", "h3", "h4", "li", "tr", "td", "th", "caption"}:
            self.text.append("\n")
        if tag == "tr" and self.table_stack:
            context = self.table_stack[-1]
            if context["occupied"] is None:
                raise ValidationError("Rich question content contains malformed table rows.")
            context["rowspan"] = {
                column: remaining - 1 for column, remaining in context["rowspan"].items() if remaining > 1
            }
            context["occupied"] = None
            context["column"] = 0
        elif tag == "table" and self.table_stack:
            context = self.table_stack[-1]
            if context["occupied"] is not None:
                raise ValidationError("Rich question content contains an unclosed table row.")
            self.table_stack.pop()
        if tag != "br":
            self.depth = max(0, self.depth - 1)

    def handle_data(self, data):
        self.text.append(data)

    def validate(self, *, field):
        if self.nodes > MAX_NODES_BY_FIELD[field]:
            raise ValidationError(f"This field may contain at most {MAX_NODES_BY_FIELD[field]:,} HTML elements.")
        if self.max_depth > MAX_DEPTH:
            raise ValidationError(f"Rich question content may be nested at most {MAX_DEPTH} levels deep.")
        if len(self.tables) > MAX_TABLES_BY_FIELD[field]:
            raise ValidationError(f"This field may contain at most {MAX_TABLES_BY_FIELD[field]} tables.")
        if any(table["rows"] > MAX_ROWS_PER_TABLE for table in self.tables):
            raise ValidationError(f"Each table may contain at most {MAX_ROWS_PER_TABLE} rows.")
        return " ".join("".join(self.text).split())


def canonicalize_question_content(raw_content: str, *, field: str) -> CanonicalQuestionContent:
    if field not in TEXT_FIELDS:
        raise ValueError("Unknown rich question field.")
    raw_content = raw_content or ""
    if len(raw_content) > MAX_RAW_BY_FIELD[field] or len(raw_content.encode("utf-8")) > MAX_RAW_BYTES_BY_FIELD[field]:
        raise ValidationError("Submitted rich question content exceeds this field's request limit.")
    try:
        canonical = canonicalize_scenario_content(raw_content).html
    except ValidationError as exc:
        raise ValidationError("Rich question content cannot be preserved safely. " + "; ".join(exc.messages)) from exc
    if len(canonical) > MAX_CANONICAL_BY_FIELD[field]:
        raise ValidationError("Canonical rich question content exceeds this field's limit.")
    inspector = _QuestionLimitInspector()
    inspector.feed(canonical)
    inspector.close()
    visible = inspector.validate(field=field)
    if len(visible) > MAX_VISIBLE_BY_FIELD[field]:
        raise ValidationError(f"Visible text may not exceed {MAX_VISIBLE_BY_FIELD[field]:,} characters in this field.")
    return CanonicalQuestionContent(canonical, visible, inspector.nodes, len(inspector.tables), inspector.cells)


def canonicalize_question_fields(payload: dict, *, content_format: str) -> tuple[dict, dict]:
    """Return canonical stored values and visible text, enforcing aggregate limits."""
    if content_format == PLAIN_TEXT:
        values = {field: (payload.get(field) or "") for field in TEXT_FIELDS}
        return values, dict(values)
    if content_format != RICH_HTML_V1:
        raise ValidationError({"content_format": ["Unsupported question content format."]})
    raw_values = {field: (payload.get(field) or "") for field in TEXT_FIELDS}
    if sum(len(value) for value in raw_values.values()) > MAX_RAW_CHARACTERS or sum(len(value.encode("utf-8")) for value in raw_values.values()) > MAX_RAW_BYTES:
        raise ValidationError({"__all__": ["Submitted rich question content exceeds the aggregate request limit."]})
    values, visible, nodes, tables, cells, errors = {}, {}, 0, 0, 0, {}
    for field, value in raw_values.items():
        try:
            result = canonicalize_question_content(value, field=field)
        except ValidationError as exc:
            errors[field] = exc.messages
            continue
        values[field], visible[field] = result.html, result.visible_text
        nodes += result.nodes
        tables += result.tables
        cells += result.cells
    if errors:
        raise ValidationError(errors)
    aggregate_errors = []
    if sum(len(value) for value in values.values()) > MAX_CANONICAL_CHARACTERS:
        aggregate_errors.append("Canonical rich question content exceeds the aggregate limit.")
    if sum(len(value) for value in visible.values()) > MAX_VISIBLE_CHARACTERS:
        aggregate_errors.append("Visible rich question text exceeds the aggregate limit.")
    if nodes > MAX_NODES or tables > MAX_TABLES or cells > MAX_TABLE_CELLS:
        aggregate_errors.append("Rich question content exceeds aggregate structural limits.")
    if aggregate_errors:
        raise ValidationError({"__all__": aggregate_errors})
    return values, visible


def visible_text(value: str, content_format: str) -> str:
    if content_format == PLAIN_TEXT:
        return (value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return canonicalize_question_content(value or "", field="question_text").visible_text


class _IdentityParser(HTMLParser):
    STRUCTURAL = {"table", "tr", "th", "td", "caption", "ul", "ol", "li", "sup", "sub"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tokens, self.text = [], []

    def _flush(self):
        value = " ".join(unicodedata.normalize("NFC", "".join(self.text)).split())
        if value:
            self.tokens.append(["text", value])
        self.text = []

    @staticmethod
    def _classes(attrs, allowed):
        classes = set(dict(attrs).get("class", "").split())
        return sorted(classes & allowed)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in {"p", "h3", "h4", "br"}:
            self.text.append("\n")
            classes = self._classes(attrs, {"tmp-align-left", "tmp-align-center", "tmp-align-right", "tmp-align-justify", *{f"tmp-indent-{number}" for number in range(1, 9)}})
            if classes:
                self._flush()
                self.tokens.append(["start", "block", classes])
            return
        if tag not in self.STRUCTURAL:
            return
        self._flush()
        details = []
        if tag in {"th", "td"}:
            details = [int(attributes.get("rowspan", "1")), int(attributes.get("colspan", "1")), self._classes(attrs, {"tmp-align-left", "tmp-align-center", "tmp-align-right", "tmp-align-justify", "tmp-valign-top", "tmp-valign-middle", "tmp-valign-bottom", "tmp-rule-single", "tmp-rule-double"})]
        elif tag == "ol":
            details = [int(attributes.get("start", "1"))]
        self.tokens.append(["start", tag, details])

    def handle_endtag(self, tag):
        if tag in {"p", "h3", "h4"}:
            self.text.append("\n")
        if tag in self.STRUCTURAL:
            self._flush()
            self.tokens.append(["end", tag])

    def handle_data(self, data):
        self.text.append(data)

    def finish(self):
        self._flush()
        return self.tokens


def identity_tokens(value: str, content_format: str) -> list:
    if content_format == PLAIN_TEXT:
        normalized = " ".join(unicodedata.normalize("NFC", value or "").split())
        return [["text", normalized]] if normalized else []
    canonical = canonicalize_question_content(value or "", field="question_text").html
    parser = _IdentityParser()
    parser.feed(canonical)
    parser.close()
    return parser.finish()
