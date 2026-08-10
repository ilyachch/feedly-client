"""On-disk caches.

Two independent caches keep the utility cheap:

* stream names — collection and feed titles change rarely, so they are refreshed once a day;
* entry metadata — what ``list`` returned, so ``triage`` can write rich log rows without the agent
  re-sending titles and URLs.
"""

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

STREAMS_TTL = timedelta(hours=24)
ENTRIES_TTL = timedelta(days=7)
ENTRIES_LIMIT = 5000


def _now() -> datetime:
    return datetime.now(UTC)


def _read_json(path: Path) -> dict[str, Any]:
    """Load a JSON object, treating a missing or corrupted file as an empty cache."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically; the unique temp name keeps concurrent runs from clobbering it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = NamedTemporaryFile(  # noqa: SIM115 - closed explicitly before the atomic replace
        "w", encoding="utf-8", dir=path.parent, prefix=f"{path.name}.", suffix=".tmp", delete=False
    )
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(handle.name, path)
    finally:
        Path(handle.name).unlink(missing_ok=True)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class StreamInfo:
    """A resolvable stream: a collection (folder) or a feed.

    ``collections`` holds every collection a feed belongs to, because Feedly allows the same feed in
    several folders and the per-feed fetch strategy must find it in all of them.
    """

    id: str
    label: str
    kind: str
    collections: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "kind": self.kind, "collections": list(self.collections)}


class StreamNameCache:
    """Cached collection/feed directory with a 24 hour lifetime."""

    def __init__(self, path: Path, ttl: timedelta = STREAMS_TTL) -> None:
        self._path = path
        self._ttl = ttl

    def load(self) -> tuple[StreamInfo, ...] | None:
        """Return cached streams, or ``None`` when absent, stale or empty.

        An empty directory is treated as a miss: caching "this account has no feeds" for a day would
        break every lookup after a transient API hiccup.
        """
        data = _read_json(self._path)
        saved_at = _parse_time(data.get("saved_at"))
        streams = data.get("streams")
        if saved_at is None or not isinstance(streams, list) or not streams:
            return None
        if _now() - saved_at > self._ttl:
            return None
        result = []
        for item in streams:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                collections = item.get("collections")
                result.append(
                    StreamInfo(
                        id=item["id"],
                        label=str(item.get("label") or item["id"]),
                        kind=str(item.get("kind") or "feed"),
                        collections=tuple(str(name) for name in collections)
                        if isinstance(collections, list)
                        else (),
                    )
                )
        return tuple(result) or None

    def save(self, streams: tuple[StreamInfo, ...]) -> None:
        """Persist the stream directory with the current timestamp."""
        _write_json(
            self._path,
            {"saved_at": _now().isoformat(), "streams": [stream.as_dict() for stream in streams]},
        )

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class EntryMeta:
    """Minimal article metadata needed for a readable triage log."""

    id: str
    title: str = ""
    source: str = ""
    url: str = ""
    published: str = ""
    stream: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "source": self.source,
            "url": self.url,
            "published": self.published,
            "stream": self.stream,
        }


class EntryMetaCache:
    """Bounded, self-expiring store of the entries most recently shown to the agent."""

    def __init__(self, path: Path, ttl: timedelta = ENTRIES_TTL, limit: int = ENTRIES_LIMIT) -> None:
        self._path = path
        self._ttl = ttl
        self._limit = limit

    def remember(self, entries: list[EntryMeta]) -> None:
        """Merge ``entries`` into the cache, dropping expired and surplus records."""
        if not entries:
            return
        stored = self._records()
        seen_at = _now().isoformat()
        for entry in entries:
            stored[entry.id] = {**entry.as_dict(), "seen_at": seen_at}
        _write_json(self._path, {"entries": self._pruned(stored)})

    def get(self, entry_id: str) -> EntryMeta | None:
        """Return cached metadata for ``entry_id`` when it is still fresh."""
        return self.get_many([entry_id]).get(entry_id)

    def get_many(self, entry_ids: Iterable[str]) -> dict[str, EntryMeta]:
        """Look several ids up in one pass, so a large triage run parses the file once."""
        records = self._records()
        found: dict[str, EntryMeta] = {}
        for entry_id in entry_ids:
            record = records.get(entry_id)
            if record is None:
                continue
            found[entry_id] = EntryMeta(
                id=entry_id,
                title=str(record.get("title") or ""),
                source=str(record.get("source") or ""),
                url=str(record.get("url") or ""),
                published=str(record.get("published") or ""),
                stream=str(record.get("stream") or ""),
            )
        return found

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)

    def _records(self) -> dict[str, dict[str, Any]]:
        data = _read_json(self._path).get("entries")
        if not isinstance(data, dict):
            return {}
        cutoff = _now() - self._ttl
        fresh = {}
        for key, value in data.items():
            if not isinstance(value, dict):
                continue
            seen_at = _parse_time(value.get("seen_at"))
            if seen_at is not None and seen_at >= cutoff:
                fresh[key] = value
        return fresh

    def _pruned(self, records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        if len(records) <= self._limit:
            return records
        ordered = sorted(records.items(), key=lambda item: str(item[1].get("seen_at") or ""), reverse=True)
        return dict(ordered[: self._limit])
