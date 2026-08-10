"""Applying the agent's verdicts: mark as read and log everything.

Only ``uninteresting`` entries are marked as read in Feedly, but every verdict — including the
``interesting`` ones that stay unread — is written to a CSV log, so the wording of the rules can be
reviewed and refined later.
"""

import csv
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TextIO

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .api import FeedlyClient, PartialMarkerError
from .cache import EntryMeta, EntryMetaCache

VERDICT_INTERESTING = "interesting"
VERDICT_UNINTERESTING = "uninteresting"

LOG_COLUMNS = (
    "timestamp",
    "verdict",
    "topic",
    "note",
    "entry_id",
    "title",
    "source",
    "stream",
    "published",
    "url",
)


class TriageInputError(ValueError):
    """The decision payload is not usable."""


class PartialTriageError(RuntimeError):
    """Feedly failed midway through marking; ``result`` describes what actually happened."""

    def __init__(self, message: str, *, result: "TriageResult") -> None:
        super().__init__(message)
        self.result = result


class Decision(BaseModel):
    """One verdict about one article."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    verdict: Literal["interesting", "uninteresting"]
    topic: str = ""
    note: str = ""

    @model_validator(mode="after")
    def _uninteresting_needs_topic(self) -> "Decision":
        if self.verdict == VERDICT_UNINTERESTING and not self.topic.strip():
            raise ValueError("an uninteresting verdict requires a topic")
        return self


@dataclass(frozen=True, slots=True)
class TriageResult:
    """Outcome of one triage run."""

    marked_read: int
    kept: int
    log_file: Path | None
    unknown_ids: tuple[str, ...]
    dry_run: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "marked_read": self.marked_read,
            "kept": self.kept,
            "log_file": str(self.log_file) if self.log_file else None,
            "unknown_ids": list(self.unknown_ids),
            "dry_run": self.dry_run,
        }


def parse_decisions(payload: str) -> tuple[Decision, ...]:
    """Parse and validate a JSON array of decisions."""
    try:
        data = json.loads(payload)
    except ValueError as error:
        raise TriageInputError(f"decisions must be valid JSON: {error}") from error
    if isinstance(data, dict) and "decisions" in data:
        data = data["decisions"]
    if not isinstance(data, list):
        raise TriageInputError("decisions must be a JSON array of objects")
    if not data:
        raise TriageInputError("decisions must contain at least one entry")
    decisions: list[Decision] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise TriageInputError(f"decision #{index + 1} must be an object")
        try:
            decisions.append(Decision.model_validate(item))
        except ValidationError as error:
            raise TriageInputError(f"decision #{index + 1} is invalid: {_first_error(error)}") from error
    counts = Counter(decision.id for decision in decisions)
    duplicates = sorted(entry_id for entry_id, count in counts.items() if count > 1)
    if duplicates:
        raise TriageInputError(f"duplicate decision ids: {', '.join(duplicates)}")
    return tuple(decisions)


def _first_error(error: ValidationError) -> str:
    first = error.errors()[0]
    location = ".".join(str(part) for part in first["loc"]) or "payload"
    return f"{location}: {first['msg']}"


def open_log_file(logs_dir: Path, moment: datetime) -> tuple[Path, TextIO]:
    """Create and open a fresh ``YYYY-MM-DD_HH-MM-SS.csv`` inside ``logs_dir``.

    Exclusive creation, rather than an existence check, keeps two runs in the same second from
    truncating each other's log.
    """
    base = moment.strftime("%Y-%m-%d_%H-%M-%S")
    for suffix in range(100):
        candidate = logs_dir / (f"{base}.csv" if suffix == 0 else f"{base}-{suffix}.csv")
        try:
            return candidate, candidate.open("x", encoding="utf-8", newline="")
        except FileExistsError:
            continue
    raise OSError(f"could not create a log file in {logs_dir}")


def _safe_cell(value: str) -> str:
    """Neutralise spreadsheet formula injection in free-text columns.

    The log is meant to be reopened in a spreadsheet, and topics and notes come from an LLM.
    """
    return f"'{value}" if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def run_triage(
    decisions: tuple[Decision, ...],
    *,
    client: FeedlyClient,
    entry_cache: EntryMetaCache,
    logs_dir: Path,
    dry_run: bool = False,
    now: datetime | None = None,
) -> TriageResult:
    """Mark uninteresting entries as read and log every verdict."""
    moment = now or datetime.now().astimezone()
    to_mark = [decision.id for decision in decisions if decision.verdict == VERDICT_UNINTERESTING]
    kept = len(decisions) - len(to_mark)
    known = entry_cache.get_many(decision.id for decision in decisions)
    unknown = tuple(decision.id for decision in decisions if decision.id not in known)

    if dry_run:
        return TriageResult(len(to_mark), kept, None, unknown, True)

    # The log is written before anything is marked: marking is irreversible in practice, so a
    # verdict must never disappear because of a later I/O or API failure.
    logs_dir.mkdir(parents=True, exist_ok=True)
    path, handle = open_log_file(logs_dir, moment)
    timestamp = moment.isoformat(timespec="seconds")
    with handle:
        writer = csv.DictWriter(handle, fieldnames=LOG_COLUMNS)
        writer.writeheader()
        for decision in decisions:
            meta = known.get(decision.id, EntryMeta(id=decision.id))
            writer.writerow(
                {
                    "timestamp": timestamp,
                    "verdict": decision.verdict,
                    "topic": _safe_cell(decision.topic),
                    "note": _safe_cell(decision.note),
                    "entry_id": decision.id,
                    "title": _safe_cell(meta.title),
                    "source": _safe_cell(meta.source),
                    "stream": _safe_cell(meta.stream),
                    "published": meta.published,
                    "url": meta.url,
                }
            )

    marked = 0
    if to_mark:
        try:
            marked = client.mark_as_read(to_mark)
        except PartialMarkerError as error:
            raise PartialTriageError(
                str(error), result=TriageResult(error.submitted, kept, path, unknown, False)
            ) from error
    return TriageResult(marked, kept, path, unknown, False)
