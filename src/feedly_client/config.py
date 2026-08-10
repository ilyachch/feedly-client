"""Credentials and settings.

Values come from the process environment; a ``.env`` file in the project root fills the gaps.
Real environment variables always win, and the file is never searched for in parent directories.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

ENV_API_KEY = "FEEDLY_API_KEY"
ENV_USER_ID = "FEEDLY_USER_ID"
ENV_DATA_DIR = "FEEDLY_DATA_DIR"
ENV_FILE = "FEEDLY_ENV_FILE"


class ConfigError(RuntimeError):
    """A required setting is missing or malformed."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything the CLI needs to talk to Feedly."""

    api_key: str
    user_id: str
    data_dir: Path | None = None


def project_root() -> Path:
    """Return the directory that owns the ``.env`` file.

    The current working directory wins when it looks like the project root; otherwise the source
    checkout that contains this package is used. Parent directories are never scanned.
    """
    cwd = Path.cwd()
    if (cwd / "pyproject.toml").exists() or (cwd / ".env").exists():
        return cwd
    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "pyproject.toml").exists():
        return source_root
    return cwd


def env_file() -> Path:
    """Path of the ``.env`` file that is consulted for missing settings."""
    override = os.environ.get(ENV_FILE)
    return Path(override).expanduser() if override else project_root() / ".env"


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    """Build :class:`Settings`, raising :class:`ConfigError` when credentials are missing."""
    environ = dict(os.environ if environ is None else environ)
    path = env_file()
    file_values = {k: v for k, v in dotenv_values(path).items() if v is not None} if path.exists() else {}

    def value(name: str) -> str:
        return (environ.get(name) or file_values.get(name) or "").strip()

    api_key = value(ENV_API_KEY)
    user_id = value(ENV_USER_ID)
    missing = [name for name, found in ((ENV_API_KEY, api_key), (ENV_USER_ID, user_id)) if not found]
    if missing:
        raise ConfigError(
            f"missing {' and '.join(missing)}; set them in the environment or in {path} (see .env.example)"
        )
    if "/" in user_id:
        raise ConfigError(f"{ENV_USER_ID} must be the bare user id, without the 'user/' prefix")
    raw_data_dir = value(ENV_DATA_DIR)
    return Settings(
        api_key=api_key,
        user_id=user_id,
        data_dir=Path(raw_data_dir).expanduser() if raw_data_dir else None,
    )
