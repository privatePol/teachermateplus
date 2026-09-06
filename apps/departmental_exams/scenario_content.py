from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser

import nh3
from django.core.exceptions import ValidationError
from django.template.defaultfilters import linebreaksbr
from django.utils.html import escape, strip_tags
from django.utils.safestring import mark_safe


PLAIN_TEXT = "PLAIN_TEXT"
RICH_HTML_V1 = "RICH_HTML_V1"

MAX_RAW_CHARACTERS = 100_000
MAX_RAW_BYTES = 200_000
MAX_CANONICAL_CHARACTERS = 50_000
MAX_NODES = 2_000
MAX_DEPTH = 32
MAX_TABLES = 10
MAX_ROWS_PER_TABLE = 100
MAX_COLUMNS_PER_ROW = 20
MAX_SPAN = 20
MAX_ORDERED_LIST_START = 10_000

ALLOWED_TAGS = {
    "p", "h3", "h4", "strong", "em", "ul", "ol", "li", "table",
    "caption", "thead", "tbody", "tfoot", "tr", "th", "td", "br",
    "sup", "sub",
}
ALIGNMENT_CLASSES = {
    "tmp-align-left", "tmp-align-center", "tmp-align-right"
}
ALIGNABLE_TAGS = {"p", "h3", "h4", "th", "td"}
DROP_WITH_CONTENT = {
    "script", "style", "iframe", "object", "embed", "svg", "form",
    "button", "input", "select", "textarea", "option", "link", "meta",
}
HTML_VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
NONVOID_DROP_WITH_CONTENT = DROP_WITH_CONTENT - HTML_VOID_ELEMENTS
TAG_MAP = {"b": "strong", "i": "em", "div": "p"}
WORD_METADATA_PATTERN = re.compile(
    r"(?:<!--|\bmso-|\bclass\s*=\s*['\"]?Mso|<\/?(?:o|v|w|st1):)", re.I
)
IMAGE_PATTERN = re.compile(r"<(?:img|svg|v:shape|v:imagedata)\b", re.I)
OMML_PATTERN = re.compile(
    r"<(?:m:)?oMath(?:Para)?\b|urn:schemas-microsoft-com:office:math", re.I
)
ALIGNMENT_PATTERN = re.compile(r"(?:^|;)\s*text-align\s*:\s*(left|center|right)\s*(?:;|$)", re.I)


@dataclass(frozen=True)
class CanonicalScenarioContent:
    html: str
    warnings: tuple[str, ...]

    @property
    def digest(self):
        return hashlib.sha256(self.html.encode("utf-8")).hexdigest()


def _bounded_positive(value, *, field, maximum):
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field} must be a positive whole number.") from exc
    if not 1 <= number <= maximum:
        raise ValidationError(f"{field} must be from 1 to {maximum}.")
    return str(number)


class _WordSemanticNormalizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output = []
        self.stack = []
        self.warnings = set()

    @property
    def has_open_suppression_boundary(self):
        return any(frame[2] for frame in self.stack)

    @staticmethod
    def _local_name(tag):
        return tag.lower().split(":")[-1]

    def _semantic_tags(self, tag, attrs):
        local = self._local_name(tag)
        attributes = {name.lower(): value or "" for name, value in attrs}
        if local in {"img", "svg", "shape", "imagedata"}:
            raise ValidationError(
                "Images and diagrams pasted from Word are not supported. Remove them and try again."
            )
        if local in {"omath", "omathpara"}:
            raise ValidationError(
                "A native Word equation was detected. Replace it with TMP LaTeX delimiters or Unicode symbols."
            )
        if local in DROP_WITH_CONTENT:
            return None
        mapped = TAG_MAP.get(local, local)
        wrappers = []
        if mapped in ALLOWED_TAGS:
            wrappers.append(mapped)
        elif local == "span":
            style = attributes.get("style", "").lower()
            if "font-weight:bold" in style.replace(" ", "") or re.search(r"font-weight\s*:\s*[6-9]00", style):
                wrappers.append("strong")
            if "font-style:italic" in style.replace(" ", ""):
                wrappers.append("em")
            if re.search(r"vertical-align\s*:\s*super", style):
                wrappers.append("sup")
            elif re.search(r"vertical-align\s*:\s*sub", style):
                wrappers.append("sub")
        return wrappers

    def _safe_attrs(self, tag, attrs):
        attributes = {name.lower(): value or "" for name, value in attrs}
        result = []
        if tag in {"th", "td"}:
            for name in ("rowspan", "colspan"):
                if attributes.get(name):
                    result.append((name, _bounded_positive(
                        attributes[name], field=name, maximum=MAX_SPAN
                    )))
        if tag == "th" and attributes.get("scope", "").lower() in {"row", "col", "rowgroup", "colgroup"}:
            result.append(("scope", attributes["scope"].lower()))
        if tag == "ol" and attributes.get("start"):
            result.append(("start", _bounded_positive(
                attributes["start"], field="Ordered-list start", maximum=MAX_ORDERED_LIST_START
            )))
        if tag in ALIGNABLE_TAGS:
            approved = next((name for name in attributes.get("class", "").split() if name in ALIGNMENT_CLASSES), None)
            if approved is None:
                match = ALIGNMENT_PATTERN.search(attributes.get("style", ""))
                if match:
                    approved = f"tmp-align-{match.group(1).lower()}"
            if approved:
                result.append(("class", approved))
        return result

    def handle_starttag(self, tag, attrs):
        local = self._local_name(tag)
        if self.has_open_suppression_boundary:
            if local in HTML_VOID_ELEMENTS:
                return
            self.stack.append((local, [], local in DROP_WITH_CONTENT))
            return
        semantic = self._semantic_tags(tag, attrs)
        if semantic is None:
            if local in HTML_VOID_ELEMENTS:
                self.warnings.add("Unsupported active or embedded Word markup was removed.")
                return
            self.stack.append((local, [], True))
            self.warnings.add("Unsupported active or embedded Word markup was removed.")
            return
        if semantic == ["br"]:
            self.output.append("<br>")
            return
        if local in HTML_VOID_ELEMENTS:
            self.warnings.add("Unsupported Word formatting was removed while visible text was preserved.")
            return
        emitted = []
        for normalized_tag in semantic:
            safe_attrs = self._safe_attrs(normalized_tag, attrs)
            rendered_attrs = "".join(
                f' {name}="{html.escape(value, quote=True)}"' for name, value in safe_attrs
            )
            self.output.append(f"<{normalized_tag}{rendered_attrs}>")
            emitted.append(normalized_tag)
        if not emitted and local not in ALLOWED_TAGS:
            self.warnings.add("Unsupported Word formatting was removed while visible text was preserved.")
        self.stack.append((local, emitted, False))

    def handle_startendtag(self, tag, attrs):
        local = self._local_name(tag)
        if local in HTML_VOID_ELEMENTS:
            self.handle_starttag(tag, attrs)
            return
        if local in NONVOID_DROP_WITH_CONTENT:
            raise ValidationError(
                "Case content contains malformed unsupported active markup. "
                "Non-void elements cannot use self-closing syntax. Remove it and try again."
            )
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        local = self._local_name(tag)
        if self.has_open_suppression_boundary:
            if not self.stack or self.stack[-1][0] != local:
                raise ValidationError(
                    "Case content contains malformed unsupported active markup. "
                    "Remove it and try again."
                )
            self.stack.pop()
            return
        matching_index = next(
            (
                index
                for index in range(len(self.stack) - 1, -1, -1)
                if self.stack[index][0] == local
            ),
            None,
        )
        if matching_index is None:
            return
        closing = self.stack[matching_index:]
        del self.stack[matching_index:]
        for _local, emitted, _suppression_root in reversed(closing):
            for normalized_tag in reversed(emitted):
                self.output.append(f"</{normalized_tag}>")

    def handle_data(self, data):
        if not self.has_open_suppression_boundary:
            self.output.append(html.escape(data, quote=False))

    def handle_comment(self, _data):
        self.warnings.add("Microsoft Word comments and conditional metadata were removed.")


class _LimitInspector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.nodes = 0
        self.depth = 0
        self.max_depth = 0
        self.tables = []
        self.table_stack = []

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if tag == "br":
            return
        self.depth += 1
        self.max_depth = max(self.max_depth, self.depth)
        if tag == "table":
            context = {
                "rows": 0,
                "current_row_occupied": None,
                "current_column": 0,
                "rowspan_occupancy": {},
            }
            self.tables.append(context)
            self.table_stack.append(context)
        elif tag == "tr" and self.table_stack:
            context = self.table_stack[-1]
            if context["current_row_occupied"] is not None:
                raise ValidationError("Case content contains malformed table rows.")
            context["rows"] += 1
            context["current_row_occupied"] = set(context["rowspan_occupancy"])
            context["current_column"] = 0
            if context["current_row_occupied"] and max(context["current_row_occupied"]) >= MAX_COLUMNS_PER_ROW:
                self._raise_column_limit()
        elif tag in {"th", "td"} and self.table_stack:
            attributes = dict(attrs)
            rowspan = int(_bounded_positive(
                attributes.get("rowspan", "1"), field="rowspan", maximum=MAX_SPAN
            ))
            colspan = int(_bounded_positive(
                attributes.get("colspan", "1"), field="colspan", maximum=MAX_SPAN
            ))
            context = self.table_stack[-1]
            occupied = context["current_row_occupied"]
            if occupied is None:
                raise ValidationError("Case content contains a table cell outside a row.")
            column = context["current_column"]
            while column in occupied:
                column += 1
            cell_columns = set(range(column, column + colspan))
            if cell_columns & occupied:
                raise ValidationError(
                    "Case content contains overlapping table cells created by rowspan and colspan."
                )
            if column + colspan > MAX_COLUMNS_PER_ROW:
                self._raise_column_limit()
            occupied.update(cell_columns)
            context["current_column"] = column + colspan
            if rowspan > 1:
                for cell_column in cell_columns:
                    context["rowspan_occupancy"][cell_column] = rowspan

    @staticmethod
    def _raise_column_limit():
        raise ValidationError(
            f"Each table row may contain at most {MAX_COLUMNS_PER_ROW} columns including merged cells."
        )

    @staticmethod
    def _finish_row(context):
        context["rowspan_occupancy"] = {
            column: remaining_rows - 1
            for column, remaining_rows in context["rowspan_occupancy"].items()
            if remaining_rows > 1
        }
        context["current_row_occupied"] = None
        context["current_column"] = 0

    def handle_startendtag(self, tag, attrs):
        self.nodes += 1

    def handle_endtag(self, tag):
        self.depth = max(self.depth - 1, 0)
        if tag == "tr" and self.table_stack:
            context = self.table_stack[-1]
            if context["current_row_occupied"] is None:
                raise ValidationError("Case content contains malformed table rows.")
            self._finish_row(context)
        elif tag == "table" and self.table_stack:
            if self.table_stack[-1]["current_row_occupied"] is not None:
                raise ValidationError("Case content contains an unclosed table row.")
            self.table_stack.pop()

    def validate(self):
        if self.nodes > MAX_NODES:
            raise ValidationError(f"Case content may contain at most {MAX_NODES:,} HTML elements.")
        if self.max_depth > MAX_DEPTH:
            raise ValidationError(f"Case content may be nested at most {MAX_DEPTH} levels deep.")
        if len(self.tables) > MAX_TABLES:
            raise ValidationError(f"A Case may contain at most {MAX_TABLES} tables.")
        if any(table["rows"] > MAX_ROWS_PER_TABLE for table in self.tables):
            raise ValidationError(f"Each table may contain at most {MAX_ROWS_PER_TABLE} rows.")


def _plain_text_to_html(value):
    paragraphs = re.split(r"\n\s*\n", value.replace("\r\n", "\n").replace("\r", "\n"))
    return "".join(
        f"<p>{escape(paragraph).replace(chr(10), '<br>')}</p>"
        for paragraph in paragraphs
        if paragraph.strip()
    )


def canonicalize_scenario_content(raw_content, *, input_format="html"):
    raw_content = raw_content or ""
    if len(raw_content) > MAX_RAW_CHARACTERS or len(raw_content.encode("utf-8")) > MAX_RAW_BYTES:
        raise ValidationError(
            f"Pasted Case content exceeds the {MAX_RAW_CHARACTERS:,}-character request limit."
        )
    if IMAGE_PATTERN.search(raw_content):
        raise ValidationError(
            "Images and diagrams pasted from Word are not supported. Remove them and try again."
        )
    if OMML_PATTERN.search(raw_content):
        raise ValidationError(
            "A native Word equation was detected. Replace it with TMP LaTeX delimiters or Unicode symbols."
        )
    warnings = set()
    if WORD_METADATA_PATTERN.search(raw_content):
        warnings.add("Microsoft Word metadata was removed during normalization.")
    source = _plain_text_to_html(raw_content) if input_format == "text" else raw_content
    normalizer = _WordSemanticNormalizer()
    try:
        normalizer.feed(source)
        normalizer.close()
    except (ValueError, OverflowError) as exc:
        raise ValidationError("Case content contains malformed table or list values.") from exc
    if normalizer.has_open_suppression_boundary:
        raise ValidationError(
            "Case content contains unclosed unsupported active markup. Remove it and try again."
        )
    warnings.update(normalizer.warnings)
    normalized = "".join(normalizer.output)
    attributes = {
        "ol": {"start"},
        "th": {"rowspan", "colspan", "scope"},
        "td": {"rowspan", "colspan"},
    }
    canonical = nh3.clean(
        normalized,
        tags=ALLOWED_TAGS,
        clean_content_tags=DROP_WITH_CONTENT,
        attributes=attributes,
        allowed_classes={tag: ALIGNMENT_CLASSES for tag in ALIGNABLE_TAGS},
        url_schemes=set(),
        url_relative="deny",
        strip_comments=True,
        link_rel=None,
    ).strip()
    if len(canonical) > MAX_CANONICAL_CHARACTERS:
        raise ValidationError(
            f"Canonical Case content may not exceed {MAX_CANONICAL_CHARACTERS:,} characters."
        )
    inspector = _LimitInspector()
    inspector.feed(canonical)
    inspector.close()
    inspector.validate()
    if not html.unescape(strip_tags(canonical)).strip():
        raise ValidationError("Case / Scenario content is required.")
    return CanonicalScenarioContent(canonical, tuple(sorted(warnings)))


def render_scenario_content(value, content_format):
    if content_format == PLAIN_TEXT:
        return linebreaksbr(value or "")
    if content_format != RICH_HTML_V1:
        return escape(value or "")
    try:
        canonical = canonicalize_scenario_content(value).html
    except ValidationError:
        return escape(value or "")
    return mark_safe(canonical)
