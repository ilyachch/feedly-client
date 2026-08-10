"""Command line interface.

Designed to be driven by an LLM agent: JSON on stdout, one job per command, no interactive prompts.
"""

import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from .api import FeedlyClient, FeedlyError
from .cache import EntryMetaCache, StreamNameCache
from .config import ConfigError, Settings, load_settings
from .counts import build_counts_report
from .entries import DEFAULT_SNIPPET_CHARS, to_meta, to_record
from .output import OutputFormat, emit, emit_error
from .paths import DataPaths
from .rules import ensure_rules_file
from .streams import AmbiguousStream, StreamDirectory, StreamNotFound
from .triage import PartialTriageError, TriageInputError, parse_decisions, run_triage

EXIT_RUNTIME_ERROR = 1
EXIT_USAGE_ERROR = 2

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Read Feedly unread articles and mark uninteresting ones as read.",
)
cache_app = typer.Typer(no_args_is_help=True, help="Maintain the local caches.")
app.add_typer(cache_app, name="cache")

FormatOption = Annotated[OutputFormat, typer.Option("--format", "-f", help="Output format.")]
RefreshOption = Annotated[bool, typer.Option("--refresh-cache", help="Refetch collection and feed names.")]


@dataclass(frozen=True, slots=True)
class Runtime:
    """Everything a command needs: settings, paths and the caches."""

    settings: Settings
    paths: DataPaths

    @property
    def streams_cache(self) -> StreamNameCache:
        return StreamNameCache(self.paths.streams_cache_file)

    @property
    def entries_cache(self) -> EntryMetaCache:
        return EntryMetaCache(self.paths.entries_cache_file)

    def client(self) -> FeedlyClient:
        return FeedlyClient(self.settings.api_key, self.settings.user_id)


def _runtime(fmt: OutputFormat) -> Runtime:
    """Load settings and prepare the data directory, or exit with the right error code."""
    try:
        settings = load_settings()
    except ConfigError as error:
        raise _fail(str(error), fmt, EXIT_USAGE_ERROR) from error
    try:
        paths = DataPaths.resolve(settings.data_dir).ensure()
        ensure_rules_file(paths.rules_file)
    except OSError as error:
        raise _fail(f"could not prepare the data directory: {error}", fmt) from error
    return Runtime(settings, paths)


def _fail(message: str, fmt: OutputFormat, code: int = EXIT_RUNTIME_ERROR, **details: object) -> typer.Exit:
    emit_error(message, fmt, **details)
    return typer.Exit(code)


@contextmanager
def _reported(fmt: OutputFormat) -> Iterator[None]:
    """Turn every expected failure into the documented JSON error and exit code."""
    try:
        yield
    except (StreamNotFound, AmbiguousStream, ValueError) as error:
        raise _fail(str(error), fmt, EXIT_USAGE_ERROR) from error
    except FeedlyError as error:
        raise _fail(str(error), fmt, status=error.status, body=error.body[:500]) from error
    except OSError as error:
        raise _fail(str(error), fmt) from error


def _read_payload(source: Path | None) -> str:
    """Read a JSON payload from a file or from stdin."""
    if source is None or str(source) == "-":
        return sys.stdin.read()
    return source.read_text(encoding="utf-8")


@app.command()
def counts(
    threshold: Annotated[
        int, typer.Option("--threshold", "-t", min=1, help="Unread count above which to split the fetch.")
    ] = 100,
    include_empty: Annotated[
        bool, typer.Option("--include-empty", help="Also list collections and feeds without unread items.")
    ] = False,
    refresh_cache: RefreshOption = False,
    fmt: FormatOption = OutputFormat.json,
) -> None:
    """Show unread counts per collection and feed with a fetch-strategy hint."""
    runtime = _runtime(fmt)
    with _reported(fmt), runtime.client() as client:
        directory = StreamDirectory.load(client, runtime.streams_cache, refresh=refresh_cache)
        report = build_counts_report(
            client.get_unread_counts(),
            directory,
            all_stream_id=client.all_stream_id,
            threshold=threshold,
            include_empty=include_empty,
        )
    emit(report, fmt, renderer="counts")


@app.command("list")
def list_entries(
    stream: Annotated[
        str | None,
        typer.Option("--stream", "-s", help="Collection or feed: id, exact label or unique substring."),
    ] = None,
    everything: Annotated[
        bool, typer.Option("--all", help="Read the combined stream of every subscription.")
    ] = False,
    limit: Annotated[int, typer.Option("--limit", "-n", min=1, help="Maximum entries to return.")] = 100,
    all_pages: Annotated[
        bool, typer.Option("--all-pages", help="Ignore --limit and read the stream to its end.")
    ] = False,
    snippet_chars: Annotated[
        int, typer.Option("--snippet-chars", min=0, help="Snippet length in characters, 0 disables it.")
    ] = DEFAULT_SNIPPET_CHARS,
    include_read: Annotated[
        bool, typer.Option("--include-read", help="Include entries that are already read.")
    ] = False,
    refresh_cache: RefreshOption = False,
    fmt: FormatOption = OutputFormat.json,
) -> None:
    """List unread articles of one stream in a compact, LLM-friendly shape."""
    runtime = _runtime(fmt)
    if bool(stream) == everything:
        raise _fail("provide either --stream or --all", fmt, EXIT_USAGE_ERROR)
    with _reported(fmt), runtime.client() as client:
        if everything:
            target_id, target_label = client.all_stream_id, "All subscriptions"
            if refresh_cache:
                StreamDirectory.load(client, runtime.streams_cache, refresh=True)
        else:
            directory = StreamDirectory.load(client, runtime.streams_cache, refresh=refresh_cache)
            resolved = directory.resolve(stream or "")
            target_id, target_label = resolved.id, resolved.label
        entries = list(
            client.iter_entries(
                target_id,
                unread_only=not include_read,
                limit=None if all_pages else limit,
            )
        )
    runtime.entries_cache.remember([to_meta(entry, stream_label=target_label) for entry in entries])
    emit(
        {
            "stream": {"id": target_id, "label": target_label},
            "count": len(entries),
            "entries": [to_record(entry, snippet_chars=snippet_chars) for entry in entries],
        },
        fmt,
        renderer="entries",
    )


@app.command()
def triage(
    input_file: Annotated[
        Path | None,
        typer.Option("--input", "-i", help="JSON file with decisions; omit or use '-' to read stdin."),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report what would happen without marking or logging.")
    ] = False,
    fmt: FormatOption = OutputFormat.json,
) -> None:
    """Apply verdicts: mark uninteresting entries as read and log every decision to CSV.

    Input is a JSON array of {"id", "verdict": "interesting"|"uninteresting", "topic", "note"}.
    """
    runtime = _runtime(fmt)
    try:
        decisions = parse_decisions(_read_payload(input_file))
    except (TriageInputError, OSError) as error:
        raise _fail(str(error), fmt, EXIT_USAGE_ERROR) from error
    with _reported(fmt), runtime.client() as client:
        try:
            result = run_triage(
                decisions,
                client=client,
                entry_cache=runtime.entries_cache,
                logs_dir=runtime.paths.logs_dir,
                dry_run=dry_run,
            )
        except PartialTriageError as error:
            raise _fail(str(error), fmt, **error.result.as_dict()) from error
    emit(result.as_dict(), fmt)


@app.command("keep-unread")
def keep_unread(
    ids: Annotated[list[str] | None, typer.Option("--id", help="Entry id; repeatable.")] = None,
    input_file: Annotated[
        Path | None, typer.Option("--input", "-i", help="JSON array of ids; '-' reads stdin.")
    ] = None,
    fmt: FormatOption = OutputFormat.json,
) -> None:
    """Undo a mistake: put entries back into the unread state."""
    runtime = _runtime(fmt)
    entry_ids = list(ids or [])
    if input_file is not None:
        try:
            payload = json.loads(_read_payload(input_file))
        except (ValueError, OSError) as error:
            raise _fail(f"could not read ids: {error}", fmt, EXIT_USAGE_ERROR) from error
        if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
            raise _fail("input must be a JSON array of entry ids", fmt, EXIT_USAGE_ERROR)
        entry_ids.extend(payload)
    entry_ids = [entry_id.strip() for entry_id in entry_ids if entry_id.strip()]
    if not entry_ids:
        raise _fail("provide at least one non-empty --id or --input", fmt, EXIT_USAGE_ERROR)
    with _reported(fmt), runtime.client() as client:
        restored = client.keep_unread(entry_ids)
    emit({"kept_unread": restored}, fmt)


@app.command()
def paths(fmt: FormatOption = OutputFormat.json) -> None:
    """Show where the rules file, logs and caches live."""
    runtime = _runtime(fmt)
    emit(runtime.paths.as_dict(), fmt)


@cache_app.command("clear")
def cache_clear(fmt: FormatOption = OutputFormat.json) -> None:
    """Drop cached stream names and entry metadata."""
    runtime = _runtime(fmt)
    with _reported(fmt):
        runtime.streams_cache.clear()
        runtime.entries_cache.clear()
    emit({"cleared": ["streams", "entries"]}, fmt)


def main() -> None:
    """Console script entry point."""
    app()


if __name__ == "__main__":
    main()
