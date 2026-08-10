import csv
import json
from datetime import datetime

import pytest

from feedly_client.api import Collection, Entry, PartialMarkerError, UnreadCounts
from feedly_client.cache import EntryMeta, EntryMetaCache, StreamInfo, StreamNameCache
from feedly_client.counts import build_counts_report
from feedly_client.entries import to_meta, to_record
from feedly_client.rules import ensure_rules_file
from feedly_client.streams import AmbiguousStream, StreamDirectory, StreamNotFound
from feedly_client.triage import (
    PartialTriageError,
    TriageInputError,
    open_log_file,
    parse_decisions,
    run_triage,
)

ALL_STREAM = "user/u1/category/global.all"


class FakeClient:
    def __init__(self, collections=()):
        self._collections = collections
        self.collection_calls = 0
        self.marked = []

    def get_collections(self):
        self.collection_calls += 1
        return self._collections

    def mark_as_read(self, ids):
        self.marked.extend(ids)
        return len(ids)


def collection(label, feeds):
    return Collection.model_validate(
        {
            "id": f"user/u1/category/{label}",
            "label": label,
            "feeds": [{"id": feed_id, "title": title} for feed_id, title in feeds],
        }
    )


@pytest.fixture
def directory():
    return StreamDirectory(
        (
            StreamInfo("user/u1/category/News", "News", "collection"),
            StreamInfo("feed/bbc", "BBC", "feed", ("News",)),
            StreamInfo("feed/cnn", "CNN News", "feed", ("News",)),
            StreamInfo("user/u1/category/Tech", "Tech", "collection"),
            StreamInfo("feed/lwn", "LWN", "feed", ("Tech",)),
        )
    )


def test_directory_is_cached_between_runs(tmp_path):
    cache = StreamNameCache(tmp_path / "streams.json")
    client = FakeClient((collection("News", [("feed/bbc", "BBC")]),))

    first = StreamDirectory.load(client, cache)
    second = StreamDirectory.load(client, cache)

    assert client.collection_calls == 1
    assert first == second
    assert first.label_for("feed/bbc") == "BBC"
    assert first.by_id("feed/bbc").collections == ("News",)


def test_directory_refresh_forces_a_fetch(tmp_path):
    cache = StreamNameCache(tmp_path / "streams.json")
    client = FakeClient((collection("News", [("feed/bbc", "BBC")]),))

    StreamDirectory.load(client, cache)
    StreamDirectory.load(client, cache, refresh=True)

    assert client.collection_calls == 2


def test_resolve_by_id_label_and_substring(directory):
    assert directory.resolve("feed/bbc").label == "BBC"
    assert directory.resolve("news").id == "user/u1/category/News"
    assert directory.resolve("LWN").id == "feed/lwn"


def test_resolve_reports_ambiguity_and_absence(directory):
    with pytest.raises(AmbiguousStream) as ambiguous:
        directory.resolve("N")
    assert len(ambiguous.value.candidates) > 1

    with pytest.raises(StreamNotFound):
        directory.resolve("nothing here")


def test_resolve_accepts_unknown_but_well_formed_ids(directory):
    assert directory.resolve("feed/https://new.example/rss").kind == "feed"


def test_counts_report_groups_and_recommends(directory):
    counts = UnreadCounts.model_validate(
        {
            "unreadcounts": [
                {"id": ALL_STREAM, "count": 310},
                {"id": "user/u1/category/News", "count": 300},
                {"id": "feed/bbc", "count": 250},
                {"id": "feed/cnn", "count": 50},
                {"id": "user/u1/category/Tech", "count": 10},
                {"id": "feed/lwn", "count": 10},
                {"id": "feed/orphan", "count": 7},
            ]
        }
    )

    report = build_counts_report(counts, directory, all_stream_id=ALL_STREAM, threshold=100)

    assert report["total_unread"] == 310
    assert report["strategy"] == "per_collection"
    news, tech = report["collections"]
    assert (news["label"], news["strategy"]) == ("News", "per_feed")
    assert (tech["label"], tech["strategy"]) == ("Tech", "collection")
    assert [feed["title"] for feed in news["feeds"]] == ["BBC", "CNN News"]
    assert report["uncategorized"] == [{"id": "feed/orphan", "title": "feed/orphan", "unread": 7}]


def test_counts_report_recommends_single_fetch_below_threshold(directory):
    counts = UnreadCounts.model_validate({"unreadcounts": [{"id": ALL_STREAM, "count": 12}]})

    report = build_counts_report(counts, directory, all_stream_id=ALL_STREAM, threshold=100)

    assert report["strategy"] == "all"
    assert report["collections"] == []


def test_counts_report_can_include_empty_streams(directory):
    counts = UnreadCounts.model_validate({"unreadcounts": [{"id": "feed/bbc", "count": 3}]})

    report = build_counts_report(
        counts, directory, all_stream_id=ALL_STREAM, threshold=100, include_empty=True
    )

    labels = {item["label"]: item["unread"] for item in report["collections"]}
    assert labels == {"News": 3, "Tech": 0}
    assert report["total_unread"] == 3


def test_counts_report_rejects_bad_threshold(directory):
    with pytest.raises(ValueError):
        build_counts_report(UnreadCounts(), directory, all_stream_id=ALL_STREAM, threshold=0)


def entry(**overrides):
    data = {
        "id": "e1",
        "title": "  Big news  ",
        "published": 1_700_000_000_000,
        "canonical": [{"href": "https://x.example/a"}],
        "summary": {"content": "<p>Some <b>body</b> text</p>"},
        "origin": {"streamId": "feed/bbc", "title": "BBC"},
        "keywords": ["war"],
    }
    return Entry.model_validate(data | overrides)


def test_entry_record_is_compact():
    record = to_record(entry(), snippet_chars=300)

    assert record == {
        "id": "e1",
        "title": "Big news",
        "source": "BBC",
        "published": "2023-11-14T22:13:20Z",
        "url": "https://x.example/a",
        "keywords": ["war"],
        "snippet": "Some body text",
    }


def test_entry_record_without_snippet_and_optional_fields():
    record = to_record(entry(keywords=[], summary=None, content=None), snippet_chars=0)

    assert "snippet" not in record
    assert "keywords" not in record


def test_entry_record_decodes_entities_in_title():
    record = to_record(entry(title="News&nbsp;&amp; more"), snippet_chars=0)

    assert record["title"] == "News & more"


def test_entry_meta_carries_the_stream_label():
    meta = to_meta(entry(), stream_label="News")

    assert meta == EntryMeta("e1", "Big news", "BBC", "https://x.example/a", "2023-11-14T22:13:20Z", "News")


def test_rules_file_is_created_once(tmp_path):
    path = tmp_path / "rules.md"
    ensure_rules_file(path)
    path.write_text("edited", encoding="utf-8")
    ensure_rules_file(path)

    assert path.read_text(encoding="utf-8") == "edited"


def test_parse_decisions_accepts_array_and_wrapper():
    payload = '[{"id": "e1", "verdict": "interesting"}]'
    wrapped = '{"decisions": [{"id": "e1", "verdict": "uninteresting", "topic": "crypto"}]}'

    assert parse_decisions(payload)[0].verdict == "interesting"
    assert parse_decisions(wrapped)[0].topic == "crypto"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("not json", "valid JSON"),
        ("{}", "JSON array"),
        ("[]", "at least one"),
        ('["x"]', "must be an object"),
        ('[{"id": "e1"}]', "verdict"),
        ('[{"id": "e1", "verdict": "maybe"}]', "verdict"),
        ('[{"id": "e1", "verdict": "uninteresting"}]', "requires a topic"),
        ('[{"id": "e1", "verdict": "interesting", "extra": 1}]', "extra"),
        ('[{"id": "e1", "verdict": "interesting"}, {"id": "e1", "verdict": "interesting"}]', "duplicate"),
    ],
)
def test_parse_decisions_rejects_bad_input(payload, message):
    with pytest.raises(TriageInputError, match=message):
        parse_decisions(payload)


@pytest.fixture
def cache_with_entry(tmp_path):
    cache = EntryMetaCache(tmp_path / "entries.json")
    cache.remember(
        [EntryMeta("e1", "Bitcoin again", "BBC", "https://x.example/a", "2023-11-14T22:13:20Z", "News")]
    )
    return cache


def test_run_triage_marks_read_and_logs_every_verdict(tmp_path, cache_with_entry):
    client = FakeClient()
    decisions = parse_decisions(
        '[{"id": "e1", "verdict": "uninteresting", "topic": "crypto", "note": "user hates it"},'
        ' {"id": "e2", "verdict": "interesting", "topic": "kubernetes"}]'
    )

    result = run_triage(
        decisions,
        client=client,
        entry_cache=cache_with_entry,
        logs_dir=tmp_path / "logs",
        now=datetime(2026, 8, 5, 14, 30, 12),
    )

    assert client.marked == ["e1"]
    assert result.marked_read == 1
    assert result.kept == 1
    assert result.unknown_ids == ("e2",)
    assert result.log_file.name == "2026-08-05_14-30-12.csv"

    rows = list(csv.DictReader(result.log_file.open(encoding="utf-8")))
    assert [row["verdict"] for row in rows] == ["uninteresting", "interesting"]
    assert rows[0]["title"] == "Bitcoin again"
    assert rows[0]["note"] == "user hates it"
    assert rows[0]["stream"] == "News"
    assert rows[1]["title"] == ""


def test_run_triage_dry_run_changes_nothing(tmp_path, cache_with_entry):
    client = FakeClient()
    decisions = parse_decisions('[{"id": "e1", "verdict": "uninteresting", "topic": "crypto"}]')
    logs = tmp_path / "logs"

    result = run_triage(decisions, client=client, entry_cache=cache_with_entry, logs_dir=logs, dry_run=True)

    assert client.marked == []
    assert result.dry_run is True
    assert result.log_file is None
    assert not logs.exists()


def test_run_triage_with_only_interesting_makes_no_api_call(tmp_path, cache_with_entry):
    client = FakeClient()
    decisions = parse_decisions('[{"id": "e1", "verdict": "interesting"}]')

    result = run_triage(decisions, client=client, entry_cache=cache_with_entry, logs_dir=tmp_path / "logs")

    assert client.marked == []
    assert result.marked_read == 0
    assert result.log_file.exists()


def test_log_file_is_never_overwritten(tmp_path):
    moment = datetime(2026, 8, 5, 14, 30, 12)
    first, handle = open_log_file(tmp_path, moment)
    handle.close()
    second, handle = open_log_file(tmp_path, moment)
    handle.close()

    assert (first.name, second.name) == ("2026-08-05_14-30-12.csv", "2026-08-05_14-30-12-1.csv")


def test_feed_in_two_collections_is_listed_in_both(tmp_path):
    client = FakeClient(
        (
            collection("News", [("feed/bbc", "BBC")]),
            collection("World", [("feed/bbc", "BBC")]),
        )
    )
    directory = StreamDirectory.load(client, StreamNameCache(tmp_path / "streams.json"))

    assert directory.by_id("feed/bbc").collections == ("News", "World")
    assert directory.feeds_of("World")[0].id == "feed/bbc"


def test_counts_total_falls_back_to_feed_counters(directory):
    counts = UnreadCounts.model_validate(
        {"unreadcounts": [{"id": "feed/bbc", "count": 5}, {"id": "user/u1/category/News", "count": 5}]}
    )

    report = build_counts_report(counts, directory, all_stream_id=ALL_STREAM, threshold=100)

    assert report["total_unread"] == 5


def test_counts_threshold_boundary_keeps_the_cheaper_strategy(directory):
    counts = UnreadCounts.model_validate(
        {
            "unreadcounts": [
                {"id": ALL_STREAM, "count": 100},
                {"id": "user/u1/category/News", "count": 100},
                {"id": "feed/bbc", "count": 100},
            ]
        }
    )

    report = build_counts_report(counts, directory, all_stream_id=ALL_STREAM, threshold=100)

    assert report["strategy"] == "all"
    assert report["collections"][0]["strategy"] == "collection"


def test_log_is_written_before_marking_and_survives_a_partial_failure(tmp_path, cache_with_entry):
    class FailingClient(FakeClient):
        def mark_as_read(self, ids):
            raise PartialMarkerError("Feedly returned HTTP 500", submitted=1, status=500)

    decisions = parse_decisions(
        '[{"id": "e1", "verdict": "uninteresting", "topic": "crypto"},'
        ' {"id": "e2", "verdict": "uninteresting", "topic": "crypto"}]'
    )

    with pytest.raises(PartialTriageError) as error:
        run_triage(
            decisions,
            client=FailingClient(),
            entry_cache=cache_with_entry,
            logs_dir=tmp_path / "logs",
        )

    result = error.value.result
    assert result.marked_read == 1
    assert result.log_file.exists()
    rows = list(csv.DictReader(result.log_file.open(encoding="utf-8")))
    assert len(rows) == 2


def test_log_neutralises_spreadsheet_formulas(tmp_path):
    cache = EntryMetaCache(tmp_path / "entries.json")
    cache.remember([EntryMeta("e1", "=HYPERLINK(1)", "BBC, News", 'say "hi"', "", "News")])
    decisions = parse_decisions(
        json.dumps([{"id": "e1", "verdict": "uninteresting", "topic": "=cmd|calc", "note": "line\nbreak"}])
    )

    result = run_triage(decisions, client=FakeClient(), entry_cache=cache, logs_dir=tmp_path / "logs")

    rows = list(csv.DictReader(result.log_file.open(encoding="utf-8")))
    assert rows[0]["topic"] == "'=cmd|calc"
    assert rows[0]["title"] == "'=HYPERLINK(1)"
    assert rows[0]["source"] == "BBC, News"
    assert rows[0]["note"] == "line\nbreak"


def test_triage_fails_loudly_when_the_log_cannot_be_written(tmp_path, cache_with_entry):
    logs = tmp_path / "logs"
    logs.mkdir()
    logs.chmod(0o500)
    client = FakeClient()
    decisions = parse_decisions('[{"id": "e1", "verdict": "uninteresting", "topic": "crypto"}]')

    try:
        with pytest.raises(OSError):
            run_triage(decisions, client=client, entry_cache=cache_with_entry, logs_dir=logs)
    finally:
        logs.chmod(0o700)

    assert client.marked == []
