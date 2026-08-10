"""Rendering of command results.

JSON is the default because the primary consumer is an LLM agent; the text renderer exists for a
human looking at the same data.
"""

import json
import sys
from enum import StrEnum
from typing import Any

from rich.console import Console
from rich.table import Table

_stdout = Console(soft_wrap=True)
_stderr = Console(stderr=True, soft_wrap=True)


class OutputFormat(StrEnum):
    """Supported renderings."""

    json = "json"
    text = "text"


def emit(data: dict[str, Any], fmt: OutputFormat, renderer: str | None = None) -> None:
    """Print ``data`` to stdout in the requested format.

    JSON is compact on purpose: the reader is a language model paying per token.
    """
    if fmt is OutputFormat.json:
        print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        return
    match renderer:
        case "counts":
            _counts_text(data)
        case "entries":
            _entries_text(data)
        case _:
            _pairs_text(data)


def emit_error(message: str, fmt: OutputFormat, **details: Any) -> None:
    """Print an error to stderr, in the requested format, never mixing it into stdout data."""
    if fmt is OutputFormat.json:
        payload = {"error": message, **{key: value for key, value in details.items() if value is not None}}
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), file=sys.stderr)
    else:
        _stderr.print(f"[red]error:[/red] {message}")
        for key, value in details.items():
            _stderr.print(f"  {key}: {value}")


def _counts_text(data: dict[str, Any]) -> None:
    _stdout.print(
        f"[bold]{data['total_unread']}[/bold] unread, "
        f"threshold {data['threshold']} -> strategy [bold]{data['strategy']}[/bold]"
    )
    table = Table("collection", "unread", "strategy", "top feeds")
    for collection in data["collections"]:
        feeds = ", ".join(f"{feed['title']} ({feed['unread']})" for feed in collection["feeds"][:5])
        table.add_row(collection["label"], str(collection["unread"]), collection["strategy"], feeds)
    for orphan in data.get("uncategorized", []):
        table.add_row(orphan["title"], str(orphan["unread"]), "collection", "[dim]uncategorized[/dim]")
    _stdout.print(table)


def _entries_text(data: dict[str, Any]) -> None:
    stream = data["stream"]
    _stdout.print(f"[bold]{stream['label']}[/bold] — {data['count']} entries")
    table = Table("#", "title", "source", "published", "id")
    for index, entry in enumerate(data["entries"], start=1):
        table.add_row(str(index), entry["title"], entry["source"], entry["published"], entry["id"])
    _stdout.print(table)


def _pairs_text(data: dict[str, Any]) -> None:
    for key, value in data.items():
        rendered = ", ".join(str(item) for item in value) if isinstance(value, list) else value
        _stdout.print(f"{key}: {rendered}")
