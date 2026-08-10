# Development guide

Conventions for anyone (human or agent) changing this repository. For *using* the CLI, read
`skills/feedly-triage/SKILL.md`; for the product decisions, read `docs/PLAN.md`.

## Toolchain

```bash
uv sync            # or: mise run sync
mise run test      # uv run pytest
mise run lint      # ruff check + ruff format --check
mise run fmt       # ruff format + ruff check --fix
```

Python ≥ 3.13, dependencies: `typer`, `httpx`, `pydantic`, `python-dotenv`, `rich`, `platformdirs`.
Dev: `pytest`, `respx`, `ruff`. Line length 110.

## Layout

```
prompts/       pi prompt template (/feedly-triage), symlinked by `mise run link`
skills/        agent skill, symlinked by `mise run link`
src/feedly_client/
  api/         Feedly Cloud v3 client: errors, pydantic models, httpx client
  config.py    credentials from env + .env (project root only)
  paths.py     data directory layout (platformdirs / FEEDLY_DATA_DIR)
  cache.py     stream-name cache (24 h) and entry-metadata cache (7 d, 5000 records)
  streams.py   id/label resolution over collections and feeds
  counts.py    unread aggregation + fetch-strategy hint
  entries.py   Entry -> compact record / cache metadata
  text.py      HTML -> plain text, snippet truncation
  triage.py    decision parsing, mark-as-read, CSV logging
  rules.py     rules.md bootstrap (never parsed by code)
  output.py    JSON / rich renderers
  cli.py       typer commands
```

## Rules of the codebase

- **No LLM logic in the utility.** Semantic classification belongs to the agent. Anything
  deterministic (counting, name resolution, marking, logging) belongs here.
- **Token economy is a feature.** Command output stays compact; new fields must earn their place.
- **JSON output is an API.** Changing a key or an exit code is a breaking change — update
  `README.md`, `SKILL.md` and tests together.
- **The API layer sends the minimal query string.** No `ct`/`cv` telemetry, stream controls only when
  a caller asks for them. Models keep `extra="allow"` so new Feedly fields never break parsing.
- **Nothing sensitive on disk in the repo.** Tokens live in `.env` (git-ignored); user data lives in
  the platform data directory.
- **Exit codes:** `0` success, `1` Feedly/IO failure, `2` usage or configuration error.
- Docstrings explain *why*, type hints explain *what*; do not restate the signature in prose.
- Every behaviour change needs a test. Tests use `respx` for HTTP and `typer.testing.CliRunner`
  for commands; no test may touch the network or the real data directory (`FEEDLY_DATA_DIR` is
  redirected to `tmp_path`).

## Working agreement

- Keep `docs/PLAN.md` as the source of intent; record finished work in `docs/COMPLETE.md`.
- Ask before guessing product behaviour; unclear requirements are resolved with the user, not invented.
