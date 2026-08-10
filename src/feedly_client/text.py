"""HTML to plain text conversion for cheap article snippets."""

import html
import re
from html.parser import HTMLParser

_SKIPPED_TAGS = {"script", "style", "noscript", "template"}
_BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article",
    "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre",
}  # fmt: skip
_WHITESPACE = re.compile(r"\s+")


class _TextExtractor(HTMLParser):
    """Collect visible text, turning block-level tags into spaces."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self._parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    @property
    def text(self) -> str:
        return "".join(self._parts)


def html_to_text(markup: str) -> str:
    """Return the visible text of ``markup`` with HTML entities decoded and whitespace collapsed."""
    if not markup:
        return ""
    parser = _TextExtractor()
    parser.feed(markup)
    parser.close()
    return _WHITESPACE.sub(" ", html.unescape(parser.text)).strip()


def truncate(text: str, limit: int) -> str:
    """Shorten ``text`` to ``limit`` characters on a word boundary, adding an ellipsis.

    ``limit <= 0`` returns an empty string, which is how the CLI disables snippets.
    """
    if limit <= 0 or not text:
        return ""
    if len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return f"{cut.rstrip()}…"


def snippet(markup: str, limit: int) -> str:
    """Convert ``markup`` to text and truncate it in one step."""
    return truncate(html_to_text(markup), limit)
