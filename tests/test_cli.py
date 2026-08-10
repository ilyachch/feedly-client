import csv
import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from feedly_client.cli import app

BASE = "https://api.feedly.com/v3"
runner = CliRunner()

COLLECTIONS = [
    {
        "id": "user/u1/category/News",
        "label": "News",
        "feeds": [{"id": "feed/bbc", "title": "BBC"}, {"id": "feed/cnn", "title": "CNN"}],
    },
    {"id": "user/u1/category/Tech", "label": "Tech", "feeds": [{"id": "feed/lwn", "title": "LWN"}]},
]
COUNTS = {
    "unreadcounts": [
        {"id": "user/u1/category/global.all", "count": 210},
        {"id": "user/u1/category/News", "count": 200},
        {"id": "feed/bbc", "count": 200},
        {"id": "user/u1/category/Tech", "count": 10},
        {"id": "feed/lwn", "count": 10},
    ]
}
ENTRY = {
    "id": "e1",
    "title": "Bitcoin again",
    "published": 1_700_000_000_000,
    "canonical": [{"href": "https://bbc.example/a"}],
    "summary": {"content": "<p>Crypto <b>news</b></p>"},
    "origin": {"streamId": "feed/bbc", "title": "BBC"},
    "keywords": ["crypto"],
}


@pytest.fixture(autouse=True)
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv("FEEDLY_API_KEY", "token")
    monkeypatch.setenv("FEEDLY_USER_ID", "u1")
    monkeypatch.setenv("FEEDLY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEEDLY_ENV_FILE", str(tmp_path / "absent.env"))
    return tmp_path / "data"


def invoke(*args, stdin: str | None = None):
    return runner.invoke(app, list(args), input=stdin)


def payload(result):
    return json.loads(result.stdout)


def error_payload(result):
    return json.loads(result.stderr)


@respx.mock
def test_counts_reports_strategy_and_names(environment):
    respx.get(f"{BASE}/collections").mock(return_value=httpx.Response(200, json=COLLECTIONS))
    respx.get(f"{BASE}/markers/counts").mock(return_value=httpx.Response(200, json=COUNTS))

    result = invoke("counts")

    assert result.exit_code == 0
    data = payload(result)
    assert data["total_unread"] == 210
    assert data["strategy"] == "per_collection"
    assert [item["label"] for item in data["collections"]] == ["News", "Tech"]
    assert data["collections"][0]["strategy"] == "per_feed"


@respx.mock
def test_counts_text_format(environment):
    respx.get(f"{BASE}/collections").mock(return_value=httpx.Response(200, json=COLLECTIONS))
    respx.get(f"{BASE}/markers/counts").mock(return_value=httpx.Response(200, json=COUNTS))

    result = invoke("counts", "--format", "text")

    assert result.exit_code == 0
    assert "per_collection" in result.stdout
    assert "News" in result.stdout


@respx.mock
def test_counts_uses_cached_names_on_second_run(environment):
    collections = respx.get(f"{BASE}/collections").mock(return_value=httpx.Response(200, json=COLLECTIONS))
    respx.get(f"{BASE}/markers/counts").mock(return_value=httpx.Response(200, json=COUNTS))

    invoke("counts")
    invoke("counts")
    assert collections.call_count == 1

    invoke("counts", "--refresh-cache")
    assert collections.call_count == 2

    invoke("cache", "clear")
    invoke("counts")
    assert collections.call_count == 3


@respx.mock
def test_list_resolves_label_and_caches_metadata(environment):
    respx.get(f"{BASE}/collections").mock(return_value=httpx.Response(200, json=COLLECTIONS))
    stream = respx.get(f"{BASE}/streams/contents").mock(
        return_value=httpx.Response(200, json={"items": [ENTRY]})
    )

    result = invoke("list", "--stream", "news")

    assert result.exit_code == 0
    data = payload(result)
    assert data["stream"] == {"id": "user/u1/category/News", "label": "News"}
    assert data["entries"] == [
        {
            "id": "e1",
            "title": "Bitcoin again",
            "source": "BBC",
            "published": "2023-11-14T22:13:20Z",
            "url": "https://bbc.example/a",
            "keywords": ["crypto"],
            "snippet": "Crypto news",
        }
    ]
    params = dict(stream.calls.last.request.url.params)
    assert params["streamId"] == "user/u1/category/News"
    assert params["unreadOnly"] == "true"
    assert (
        json.loads((environment / "cache" / "entries.json").read_text())["entries"]["e1"]["stream"] == "News"
    )


@respx.mock
def test_list_all_uses_global_stream_without_collections_call(environment):
    collections = respx.get(f"{BASE}/collections").mock(return_value=httpx.Response(200, json=COLLECTIONS))
    stream = respx.get(f"{BASE}/streams/contents").mock(return_value=httpx.Response(200, json={"items": []}))

    result = invoke("list", "--all", "--limit", "5")

    assert result.exit_code == 0
    assert collections.call_count == 0
    assert dict(stream.calls.last.request.url.params)["streamId"] == "user/u1/category/global.all"
    assert payload(result)["count"] == 0


@respx.mock
def test_list_can_disable_snippets(environment):
    respx.get(f"{BASE}/streams/contents").mock(return_value=httpx.Response(200, json={"items": [ENTRY]}))

    result = invoke("list", "--all", "--snippet-chars", "0")

    assert "snippet" not in payload(result)["entries"][0]


@respx.mock
def test_list_include_read_disables_unread_filter(environment):
    stream = respx.get(f"{BASE}/streams/contents").mock(return_value=httpx.Response(200, json={"items": []}))

    invoke("list", "--all", "--include-read")

    assert dict(stream.calls.last.request.url.params)["unreadOnly"] == "false"


def test_list_requires_a_target(environment):
    result = invoke("list")

    assert result.exit_code == 2
    assert "either --stream or --all" in error_payload(result)["error"]


@respx.mock
def test_list_reports_unknown_stream(environment):
    respx.get(f"{BASE}/collections").mock(return_value=httpx.Response(200, json=COLLECTIONS))

    result = invoke("list", "--stream", "sport")

    assert result.exit_code == 2
    assert "no collection or feed matches" in error_payload(result)["error"]


@respx.mock
def test_list_reports_ambiguous_stream(environment):
    respx.get(f"{BASE}/collections").mock(return_value=httpx.Response(200, json=COLLECTIONS))

    result = invoke("list", "--stream", "N")

    assert result.exit_code == 2
    assert "matches several streams" in error_payload(result)["error"]


@respx.mock
def test_triage_marks_read_and_writes_log(environment):
    respx.get(f"{BASE}/streams/contents").mock(return_value=httpx.Response(200, json={"items": [ENTRY]}))
    markers = respx.post(f"{BASE}/markers").mock(return_value=httpx.Response(200, json={}))
    invoke("list", "--all")

    decisions = json.dumps(
        [
            {"id": "e1", "verdict": "uninteresting", "topic": "crypto"},
            {"id": "e2", "verdict": "interesting", "topic": "kubernetes", "note": "keep"},
        ]
    )
    result = invoke("triage", stdin=decisions)

    assert result.exit_code == 0
    data = payload(result)
    assert (data["marked_read"], data["kept"], data["unknown_ids"]) == (1, 1, ["e2"])
    assert json.loads(markers.calls.last.request.read())["entryIds"] == ["e1"]

    with open(data["log_file"], encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [(row["verdict"], row["title"]) for row in rows] == [
        ("uninteresting", "Bitcoin again"),
        ("interesting", ""),
    ]


@respx.mock
def test_triage_dry_run_does_not_touch_feedly(environment):
    markers = respx.post(f"{BASE}/markers").mock(return_value=httpx.Response(200, json={}))

    result = invoke(
        "triage", "--dry-run", stdin=json.dumps([{"id": "e1", "verdict": "uninteresting", "topic": "crypto"}])
    )

    data = payload(result)
    assert data["dry_run"] is True and data["log_file"] is None
    assert markers.call_count == 0
    assert not list((environment / "logs").glob("*.csv"))


def test_triage_reads_input_file(environment, tmp_path):
    source = tmp_path / "decisions.json"
    source.write_text(json.dumps([{"id": "e1", "verdict": "interesting"}]), encoding="utf-8")

    result = invoke("triage", "--input", str(source))

    assert result.exit_code == 0
    assert payload(result)["kept"] == 1


def test_triage_rejects_invalid_payload(environment):
    result = invoke("triage", stdin='[{"id": "e1", "verdict": "uninteresting"}]')

    assert result.exit_code == 2
    assert "requires a topic" in error_payload(result)["error"]


@respx.mock
def test_keep_unread_accepts_ids_and_stdin(environment):
    markers = respx.post(f"{BASE}/markers").mock(return_value=httpx.Response(200, json={}))

    result = invoke("keep-unread", "--id", "e1", "--id", "e2")
    assert payload(result)["kept_unread"] == 2

    result = invoke("keep-unread", "--input", "-", stdin=json.dumps(["e3"]))
    assert payload(result)["kept_unread"] == 1
    assert json.loads(markers.calls.last.request.read())["action"] == "keepUnread"


def test_keep_unread_requires_input(environment):
    result = invoke("keep-unread")

    assert result.exit_code == 2
    assert "at least one non-empty --id" in error_payload(result)["error"]


def test_paths_creates_layout_and_rules_file(environment):
    result = invoke("paths")

    data = payload(result)
    assert data["data_dir"] == str(environment)
    assert (environment / "rules.md").read_text(encoding="utf-8").startswith("# Feedly triage rules")
    assert (environment / "logs").is_dir()


def test_missing_credentials_exit_with_usage_error(monkeypatch, environment):
    monkeypatch.delenv("FEEDLY_API_KEY")

    result = invoke("paths")

    assert result.exit_code == 2
    assert "FEEDLY_API_KEY" in error_payload(result)["error"]


@respx.mock
def test_api_failure_is_reported_as_runtime_error(environment):
    respx.get(f"{BASE}/collections").mock(return_value=httpx.Response(403, text="forbidden"))

    result = invoke("counts")

    assert result.exit_code == 1
    data = error_payload(result)
    assert data["status"] == 403
    assert data["body"] == "forbidden"


@respx.mock
def test_list_all_pages_walks_every_page(environment):
    respx.get(f"{BASE}/streams/contents").mock(
        side_effect=[
            httpx.Response(200, json={"items": [ENTRY], "continuation": "c1"}),
            httpx.Response(200, json={"items": [ENTRY | {"id": "e2"}]}),
        ]
    )

    result = invoke("list", "--all", "--all-pages")

    assert [entry["id"] for entry in payload(result)["entries"]] == ["e1", "e2"]


def test_keep_unread_rejects_blank_ids(environment):
    result = invoke("keep-unread", "--id", "  ")

    assert result.exit_code == 2
    assert "non-empty" in error_payload(result)["error"]


@respx.mock
def test_triage_reports_a_partial_marker_failure_with_the_log(environment):
    respx.get(f"{BASE}/streams/contents").mock(return_value=httpx.Response(200, json={"items": [ENTRY]}))
    respx.post(f"{BASE}/markers").mock(return_value=httpx.Response(500, text="boom"))
    invoke("list", "--all")

    result = invoke("triage", stdin=json.dumps([{"id": "e1", "verdict": "uninteresting", "topic": "x"}]))

    assert result.exit_code == 1
    data = error_payload(result)
    assert data["marked_read"] == 0
    assert data["log_file"].endswith(".csv")


def test_unwritable_data_dir_is_reported_as_a_runtime_error(tmp_path, monkeypatch):
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    blocked.chmod(0o500)
    monkeypatch.setenv("FEEDLY_DATA_DIR", str(blocked / "data"))

    try:
        result = invoke("paths")
    finally:
        blocked.chmod(0o700)

    assert result.exit_code == 1
    assert "data directory" in error_payload(result)["error"]


@respx.mock
def test_error_body_is_truncated_and_token_free(environment, monkeypatch):
    monkeypatch.setenv("FEEDLY_API_KEY", "s3cr3t-abc")
    respx.get(f"{BASE}/collections").mock(return_value=httpx.Response(403, text="s3cr3t-abc " + "x" * 1000))

    result = invoke("counts")

    data = error_payload(result)
    assert len(data["body"]) == 500
    assert "s3cr3t-abc" not in result.stdout + result.stderr
