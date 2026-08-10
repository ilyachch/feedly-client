---
name: feedly-triage
description: Triage a Feedly account with the `feedly` CLI - inspect unread counts, read compact article listings, summarise the topics for the user, record the user's topic preferences in a rules file, and mark uninteresting articles as read while logging every verdict. Use when the user asks to read, review, clean up or filter their Feedly news.
compatibility: Requires the feedly-client project (`uv run feedly`) with FEEDLY_API_KEY and FEEDLY_USER_ID configured.
---

# Feedly triage

Goal: leave the user only the articles they care about. You do the semantic judgement; the CLI does
everything else (fetching, name resolution, marking as read, logging).

All commands print compact JSON on stdout and errors on stderr. Run them from the `feedly-client`
project directory (`uv run feedly …`) or use the installed `feedly` command.

## 1. Load the rules first

```bash
feedly paths
```

Read `rules_file` (Markdown). It lists topics the user already judged, globally and per source.
**"Interesting" always wins over "not interesting".** Never re-ask about a topic already recorded
there; apply it silently.

## 2. Decide how much to fetch

```bash
feedly counts
```

Use the `strategy` fields, do not invent your own thresholds:

- top-level `"strategy": "all"` → one pass over everything: `feedly list --all`
- top-level `"per_collection"` → handle collections one at a time, largest first
  - collection `"strategy": "collection"` → `feedly list --stream "<label>"`
  - collection `"strategy": "per_feed"` → iterate its `feeds`, `feedly list --stream "<feed title>"`

## 3. Read a batch

```bash
feedly list --stream "News" --limit 100
```

Each entry has `id`, `title`, `source`, `published`, `url`, `keywords` and a short `snippet`.
Options: `--all` (everything), `--all-pages` (ignore the limit), `--snippet-chars N` (`0` = titles
and keywords only, cheapest), `--include-read`.

Work in batches of at most ~100 entries; process one stream fully before moving to the next.

## 4. Classify

For every entry decide, using the rules file plus what the user says in the conversation:

- matches an "interesting" rule → `interesting`
- matches a "not interesting" rule (global, or scoped to that source) → `uninteresting`
- unclear → leave it out of the payload; it stays unread and you report it to the user

Then report to the user in **topic groups**, not article by article: topic, article count, a couple
of example headlines. Ask which of the still-unknown topics are uninteresting.

## 5. Record the user's answer

Append the user's new decisions to the rules file yourself (plain Markdown, one topic per bullet):

```markdown
## Not interesting

### Everywhere
- crypto
- celebrity gossip

### Source: Hacker News
- Show HN self-promotion
```

Use `### Source: <name>` only when the user restricted the topic to that source.

## 6. Apply

Send verdicts for **every** article you judged — both kept and dropped; only the uninteresting ones
are marked as read, but all of them are logged for later review:

```bash
feedly triage <<'JSON'
[
  {"id": "AbC=_1", "verdict": "uninteresting", "topic": "crypto"},
  {"id": "AbC=_2", "verdict": "interesting", "topic": "kubernetes", "note": "optional comment"}
]
JSON
```

Rules for the payload:

- `topic` is required for `uninteresting`, optional for `interesting`
- one object per id, no duplicates, no extra fields
- send it right after the `list` that produced those ids (metadata for the log comes from that cache)
- `--dry-run` previews counts without marking or logging

Response: `{"marked_read", "kept", "log_file", "unknown_ids", "dry_run"}`. A non-empty
`unknown_ids` means those ids were not in the last listings — check you did not invent or truncate an id.

On exit code 1 the same fields arrive on stderr inside the error payload: the CSV is always written
first, so `marked_read` tells you how many articles Feedly really accepted before failing. Re-send
the remaining ids rather than the whole batch.

Mistake? `feedly keep-unread --id <entry-id>` puts articles back.

## 7. Finish

Tell the user: how many articles were marked read per topic, how many remain unread, and where the
CSV log is. Suggest reviewing the log if a topic wording looked risky.

## Cost discipline

- Prefer `--snippet-chars 0` when titles and keywords are enough; raise it only for ambiguous items.
- Never fetch a stream twice for the same triage round.
- Group articles by topic before talking to the user; never dump raw listings into the chat.
