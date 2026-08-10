"""Compact article records handed to the agent."""

from typing import Any

from .api import Entry
from .cache import EntryMeta
from .text import html_to_text, snippet

DEFAULT_SNIPPET_CHARS = 300


def _published(entry: Entry) -> str:
    moment = entry.published_at
    return moment.isoformat().replace("+00:00", "Z") if moment else ""


def to_record(entry: Entry, *, snippet_chars: int = DEFAULT_SNIPPET_CHARS) -> dict[str, Any]:
    """Convert an API entry into the smallest useful representation for an LLM."""
    record: dict[str, Any] = {
        "id": entry.id,
        "title": html_to_text(entry.title or ""),
        "source": entry.source_title or entry.source_id or "",
        "published": _published(entry),
        "url": entry.url or "",
    }
    if entry.keywords:
        record["keywords"] = list(entry.keywords)
    text = snippet(entry.body_html, snippet_chars)
    if text:
        record["snippet"] = text
    return record


def to_meta(entry: Entry, *, stream_label: str) -> EntryMeta:
    """Build the cache record that lets ``triage`` write a readable log row."""
    return EntryMeta(
        id=entry.id,
        title=(entry.title or "").strip(),
        source=entry.source_title or entry.source_id or "",
        url=entry.url or "",
        published=_published(entry),
        stream=stream_label,
    )
