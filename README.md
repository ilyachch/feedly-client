# feedly-client

A Feedly triage CLI built for LLM agents. It reads unread articles, marks the ones the user does not
care about as read, and logs every verdict to CSV so the filtering rules can be reviewed later.

The split of work is deliberate: the agent only does semantic judgement, the utility does everything
deterministic — fetching, name resolution, caching, marking, logging.

## Install

```bash
uv sync
cp .env.example .env   # fill in FEEDLY_API_KEY and FEEDLY_USER_ID
uv run feedly --help
```

`FEEDLY_USER_ID` is the bare user id, without the `user/` prefix. Real environment variables take
precedence over `.env`, which is read from the project root only.

## Data location

Rules, logs and caches live outside the repository, in the platform data directory
(`~/.local/share/feedly-client` on Linux), overridable with `FEEDLY_DATA_DIR`:

```
rules.md            topic preferences, plain Markdown, maintained by the agent
logs/<ts>.csv       one file per triage run, every verdict
cache/streams.json  collection and feed names, refreshed daily
cache/entries.json  metadata of recently listed entries, used to write rich logs
```

`feedly paths` prints all of them.

## Commands

Every command prints JSON by default; `--format text` renders tables for humans. Results go to
**stdout**, errors (`{"error": …}`) to **stderr**. Real JSON output is compact — the samples below
are indented for readability.

### `feedly counts`

Unread counts per collection and feed with a fetch-strategy hint.

```json
{
  "total_unread": 210,
  "threshold": 100,
  "strategy": "per_collection",
  "collections": [
    {"id": "user/u1/category/News", "label": "News", "unread": 200, "strategy": "per_feed",
     "feeds": [{"id": "feed/bbc", "title": "BBC", "unread": 200}]}
  ],
  "uncategorized": []
}
```

`strategy` is `all` when everything fits under `--threshold` (default 100), otherwise
`per_collection`; each collection is then `collection` or `per_feed` by the same rule.
Empty streams are hidden unless `--include-empty` is given.

### `feedly list`

```bash
feedly list --stream "News" --limit 100
feedly list --all --snippet-chars 0
```

`--stream` accepts a stream id, an exact label or a unique substring of a collection or feed name.
Unread only by default (`--include-read` to widen), `--all-pages` reads a stream to its end,
`--snippet-chars` controls the plain-text snippet (0 disables it).

```json
{
  "stream": {"id": "user/u1/category/News", "label": "News"},
  "count": 1,
  "entries": [
    {"id": "AbC=_1", "title": "Bitcoin hits new high", "source": "BBC",
     "published": "2023-11-14T22:13:20Z", "url": "https://bbc.example/a",
     "keywords": ["crypto", "bitcoin"], "snippet": "Crypto news body"}
  ]
}
```

### `feedly triage`

Reads a JSON array of verdicts from stdin (or `--input file`); a `{"decisions": [...]}` wrapper is
also accepted:

```bash
feedly triage <<'JSON'
[{"id": "AbC=_1", "verdict": "uninteresting", "topic": "crypto"},
 {"id": "AbC=_2", "verdict": "interesting", "topic": "kubernetes", "note": "optional"}]
JSON
```

Only `uninteresting` entries are marked as read; **all** verdicts are written to
`logs/YYYY-MM-DD_HH-MM-SS.csv` with columns
`timestamp, verdict, topic, note, entry_id, title, source, stream, published, url`.
`topic` is required for `uninteresting`. `--dry-run` marks nothing and writes no log.

```json
{"marked_read": 1, "kept": 1, "log_file": "…/logs/2026-08-10_15-01-38.csv",
 "unknown_ids": ["AbC=_2"], "dry_run": false}
```

`unknown_ids` are ids that were not in a recent `list` result, so their log row has empty metadata.

The CSV is written **before** anything is marked as read, so a verdict can never be lost to a later
failure. If Feedly rejects part of a batch, the command exits with code 1 and the error payload
carries the same fields, with `marked_read` set to what Feedly actually accepted.

### `feedly keep-unread`

```bash
feedly keep-unread --id AbC=_1 --id AbC=_2
echo '["AbC=_1"]' | feedly keep-unread --input -
```

### `feedly paths` and `feedly cache clear`

Show the data locations (creating `rules.md` from a template on first run) and drop the caches.

Exit codes: `0` success, `1` Feedly or I/O failure, `2` bad usage or missing configuration.

Caveat: `.env` is read from the current directory when it holds one, otherwise from the source
checkout. Set `FEEDLY_ENV_FILE` to pin it explicitly.

## Agent skill and prompt

* `skills/feedly-triage/SKILL.md` — the full workflow, loaded on demand by an agent.
* `prompts/feedly-triage.md` — a pi prompt template that starts a triage session with `/feedly-triage`
  (optionally `/feedly-triage "News"` to scope it to one folder or feed). It is written for small,
  cheap models: the whole pipeline is spelled out instead of relying on the model to open the skill.

Link both into the project-local pi directory:

```bash
mise run link   # .pi/skills/feedly-triage and .pi/prompts/feedly-triage.md
```

## Development

See [AGENTS.md](AGENTS.md). Tasks: `mise run test`, `mise run lint`, `mise run fmt`.
Plan and progress live in [docs/PLAN.md](docs/PLAN.md) and [docs/COMPLETE.md](docs/COMPLETE.md).
