"""Display-only MCQ spacer compaction, after the existing rich-content guard.

Never use this projection for saving, identity, digests, generation or caselets.
Automatically marked empty MCQ spacers may compact; protected structure and
nonblank text/formatting remain independent of that presentation policy.
"""
from dataclasses import dataclass, field
from html import escape as escape_attribute, unescape
from html.parser import HTMLParser
import re

from django.core.exceptions import ValidationError
from django.utils.html import escape
from django.utils.safestring import mark_safe

from .question_content import PLAIN_TEXT, RICH_HTML_V1, canonicalize_question_content


_INLINE = {"strong", "em", "u", "sup", "sub", "span"}
_BLOCKS = {"p", "h3", "h4"}
_VOID = {"br", "img", "hr", "wbr", "input", "meta", "link", "source"}
_MATH_START = re.compile(r"\\(?:\(|\[)")


def _blank(value):
    return not unescape(value).replace("\xa0", " ").strip()


def _text_parts(raw):
    """Keep complete math spans opaque; uncertain/preformatted text fails closed."""
    text = unescape(raw)
    if "\f" in text or "\t" in text or re.search(r"\S[ ]{2,}\S", text):
        return None
    result, cursor = [], 0

    def prose(value):
        if re.search(r"\\(?:begin\s*\{|\)|\])", unescape(value)):
            return False
        for index, piece in enumerate(value.split("\n")):
            if index:
                result.append(_Text("\n"))
            if piece:
                result.append(_Text(piece))
        return True

    while match := _MATH_START.search(raw, cursor):
        close = "\\)" if match.group() == "\\(" else "\\]"
        end = raw.find(close, match.end())
        if end < 0 or not prose(raw[cursor:match.start()]):
            return None
        result.append(_Text(raw[match.start():end + len(close)]))
        cursor = end + len(close)
    if not prose(raw[cursor:]):
        return None
    return result


@dataclass
class _Text:
    raw: str
    removed: bool = False


@dataclass
class _Element:
    tag: str
    attrs: tuple = ()
    start: str = ""
    end: str = ""
    children: list = field(default_factory=list)
    removed: bool = False


class _DisplayTree(HTMLParser):
    def __init__(self, value):
        super().__init__(convert_charrefs=False)
        self.root = _Element("")
        self.stack = [self.root]
        self.feed(value)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = _Element(tag, tuple(attrs), self.get_starttag_text(), "" if tag in _VOID else f"</{tag}>")
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(_Element(tag, tuple(attrs), self.get_starttag_text()))

    def handle_endtag(self, tag):
        if len(self.stack) > 1 and self.stack[-1].tag == tag:
            self.stack.pop()

    def handle_data(self, raw):
        children = self.stack[-1].children
        if children and isinstance(children[-1], _Text):
            children[-1].raw += raw
        else:
            children.append(_Text(raw))

    def handle_entityref(self, name):
        self.handle_data(f"&{name};")

    def handle_charref(self, name):
        self.handle_data(f"&#{name};")


def _html(node):
    if node.removed:
        return ""
    if isinstance(node, _Text):
        return node.raw
    return node.start + "".join(_html(child) for child in node.children) + node.end


def _protected(node):
    if isinstance(node, _Text):
        return _text_parts(node.raw) is None
    if node.tag not in _INLINE | _BLOCKS | {"br", ""}:
        return True
    attrs = dict(node.attrs)
    classes = set((attrs.get("class") or "").split())
    if any("page-break" in name or "pagebreak" in name or "katex" in name for name in classes):
        return True
    if any(name in attrs for name in ("data-page-break", "data-preserve-whitespace")):
        return True
    if re.search(r"(?:page-break|break-(?:before|after)|white-space\s*:\s*pre)", attrs.get("style") or "", re.I):
        return True
    return any(_protected(child) for child in node.children)


def _spacer(node):
    if isinstance(node, _Text):
        return _blank(node.raw)
    if node.tag not in _BLOCKS | _INLINE | {"br"} or _protected(node):
        return False
    return all(_spacer(child) for child in node.children)


def _leaves(node):
    """Expose prose line breaks across empty/adjacent inline formatting marks."""
    children = []
    for child in node.children:
        if isinstance(child, _Text):
            parts = _text_parts(child.raw)
            children.extend(parts if parts is not None else [child])
        else:
            children.append(child)
    node.children = children
    for child in children:
        if isinstance(child, _Text) or child.tag == "br":
            yield child
        elif child.tag in _INLINE:
            yield from _leaves(child)
        else:
            yield child


def _break(node):
    return (isinstance(node, _Text) and node.raw == "\n") or (isinstance(node, _Element) and node.tag == "br")


def _compact_inline(node):
    if _protected(node):
        return
    pending = []
    seen_text = False

    def flush(trailing=False):
        breaks = [index for index, token in enumerate(pending) if _break(token)]
        if breaks:
            keep = 0 if trailing or not seen_text else 2
            for index in breaks[keep:]:
                pending[index].removed = True
            # Preserve horizontal whitespace on meaningful lines; discard only
            # spacing within dropped leading/trailing/redundant blank lines.
            for index, token in enumerate(pending):
                if _break(token):
                    continue
                if (not seen_text and index < breaks[-1]) or (trailing and index > breaks[0]) or (
                    seen_text and not trailing and len(breaks) > 2 and breaks[1] < index < breaks[-1]
                ):
                    token.removed = True
        pending.clear()

    for token in _leaves(node):
        if _break(token) or (isinstance(token, _Text) and _blank(token.raw)):
            pending.append(token)
        else:
            flush()
            seen_text = True
    flush(trailing=True)

    def prune(parent):
        for child in parent.children:
            if isinstance(child, _Element) and child.tag in _INLINE:
                prune(child)
                if not any(_html(part) for part in child.children):
                    child.removed = True

    prune(node)


def _empty_paragraph(node):
    attrs = dict(node.attrs) if isinstance(node, _Element) else {}
    classes = (attrs.get("class") or "").split()
    if "tmp-preserve" not in classes:
        classes.append("tmp-preserve")
    attrs["class"] = " ".join(classes)
    attrs = tuple(attrs.items())
    start = "<p" + "".join(f' {name}="{escape_attribute(value or "", quote=True)}"' for name, value in attrs) + ">"
    return _Element("p", attrs, start, "</p>")


def _root_inline(node):
    # Unknown/protected structure is treated conservatively as inline here.
    return isinstance(node, _Text) or node.tag not in _BLOCKS | {"br", "table", "ul", "ol"}


def _compact_verified_html(value):
    """Idempotent projection; caller must verify canonical HTML first."""
    root = _DisplayTree(value).root
    if not any(isinstance(child, _Element) and child.tag in _BLOCKS for child in root.children):
        _compact_inline(root)
        return _html(root)
    result, pending = [], []
    for index, child in enumerate(root.children):
        if isinstance(child, _Text) and _blank(child.raw):
            # Keep word separators in an inline run, even when another root
            # child is a block. Only block indentation/outer blanks are dropped.
            if 0 < index < len(root.children) - 1 and all(
                _root_inline(root.children[adjacent]) for adjacent in (index - 1, index + 1)
            ):
                result.append(child)
            continue
        if isinstance(child, _Element) and child.tag == "br":
            pending.append(child)
            continue
        if isinstance(child, _Element) and child.tag in _BLOCKS:
            if _spacer(child):
                pending.append(child)
                continue
            _compact_inline(child)
        if pending and result:
            result.append(_empty_paragraph(pending[0]))
        pending.clear()
        result.append(child)
    # Leading/trailing spacer blocks never enter the result.
    root.children = result
    return _html(root)


def _compact_plain_text(value):
    # Construct a text-only tree: never parse plain academic text as HTML.
    root = _Element("", children=[_Text(str(escape(value.replace("\r\n", "\n").replace("\r", "\n"))))])
    _compact_inline(root)
    return unescape(_html(root))


def render_questionnaire_content(value, content_format, *, field="question_text"):
    value = value or ""
    if content_format == PLAIN_TEXT:
        return escape(_compact_plain_text(value))
    if content_format != RICH_HTML_V1:
        return escape(value)
    try:
        canonical = canonicalize_question_content(value, field=field).html
    except ValidationError:
        return escape(value)
    if canonical != value:
        return escape(value)
    return mark_safe(_compact_verified_html(canonical))
