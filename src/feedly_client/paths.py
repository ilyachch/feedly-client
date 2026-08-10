"""Locations of the utility's persistent files.

Everything lives outside the repository, in the platform data directory (or ``FEEDLY_DATA_DIR``),
so rules and logs never end up in version control.
"""

from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path

APP_NAME = "feedly-client"


@dataclass(frozen=True, slots=True)
class DataPaths:
    """Resolved paths of the data directory contents."""

    root: Path

    @classmethod
    def resolve(cls, data_dir: Path | None = None) -> "DataPaths":
        """Return the data layout rooted at ``data_dir`` or the platform default."""
        root = data_dir if data_dir is not None else user_data_path(APP_NAME, appauthor=False)
        return cls(root=Path(root).expanduser().resolve())

    @property
    def rules_file(self) -> Path:
        """Markdown file with the user's topic preferences, maintained by the agent."""
        return self.root / "rules.md"

    @property
    def logs_dir(self) -> Path:
        """Directory holding one CSV file per triage run."""
        return self.root / "logs"

    @property
    def cache_dir(self) -> Path:
        """Directory holding cached stream names and entry metadata."""
        return self.root / "cache"

    @property
    def streams_cache_file(self) -> Path:
        """Cached collection/feed names."""
        return self.cache_dir / "streams.json"

    @property
    def entries_cache_file(self) -> Path:
        """Cached metadata of recently listed entries."""
        return self.cache_dir / "entries.json"

    def ensure(self) -> "DataPaths":
        """Create the directory structure if it does not exist yet."""
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        return self

    def as_dict(self) -> dict[str, str]:
        """Serialisable view used by the ``paths`` command."""
        return {
            "data_dir": str(self.root),
            "rules_file": str(self.rules_file),
            "logs_dir": str(self.logs_dir),
            "streams_cache": str(self.streams_cache_file),
            "entries_cache": str(self.entries_cache_file),
        }
