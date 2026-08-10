import json

import httpx
import pytest
import respx

from feedly_client.api import FeedlyClient, FeedlyError, PartialMarkerError

BASE = "https://api.feedly.com/v3"

COLLECTIONS = [
    {
        "id": "user/u1/category/News",
        "label": "News",
        "feeds": [{"id": "feed/https://bbc.example/rss", "title": "BBC", "website": "https://bbc.example"}],
    }
]
COUNTS = {
    "unreadcounts": [
        {"id": "feed/https://bbc.example/rss", "count": 7, "updated": 1_700_000_000_000},
        {"id": "user/u1/category/News", "count": 7},
    ],
    "updated": 1_700_000_000_001,
}
ENTRY = {
    "id": "entry-1",
    "title": "An article",
    "published": 1_700_000_000_000,
    "unread": True,
    "canonical": [{"href": "https://bbc.example/a", "type": "text/html"}],
    "summary": {"content": "<p>Body</p>", "direction": "ltr"},
    "origin": {"streamId": "feed/https://bbc.example/rss", "title": "BBC"},
    "keywords": ["war", "politics"],
    "somethingNew": {"feedly": "invented this"},
}


TOKEN = "s3cr3t-abc"


@pytest.fixture
def client():
    with FeedlyClient(TOKEN, "u1") as instance:
        yield instance


@respx.mock
def test_collections_request_has_no_query_string(client):
    route = respx.get(f"{BASE}/collections").mock(return_value=httpx.Response(200, json=COLLECTIONS))

    collections = client.get_collections()

    assert route.calls.last.request.url.query == b""
    assert route.calls.last.request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert collections[0].label == "News"
    assert collections[0].feeds[0].title == "BBC"


@respx.mock
def test_unread_counts_parses_lowercase_key(client):
    respx.get(f"{BASE}/markers/counts").mock(return_value=httpx.Response(200, json=COUNTS))

    counts = client.get_unread_counts()

    assert {c.id: c.count for c in counts.unread_counts}["user/u1/category/News"] == 7


@respx.mock
def test_stream_contents_sends_only_requested_controls(client):
    route = respx.get(f"{BASE}/streams/contents").mock(
        return_value=httpx.Response(200, json={"id": "s", "items": [ENTRY]})
    )

    page = client.get_stream_contents("s")

    assert dict(route.calls.last.request.url.params) == {"streamId": "s"}
    entry = page.items[0]
    assert entry.url == "https://bbc.example/a"
    assert entry.source_title == "BBC"
    assert entry.body_html == "<p>Body</p>"
    assert entry.published_at.isoformat() == "2023-11-14T22:13:20+00:00"
    assert entry.model_extra["somethingNew"] == {"feedly": "invented this"}


@respx.mock
def test_stream_contents_sends_all_controls(client):
    route = respx.get(f"{BASE}/streams/contents").mock(return_value=httpx.Response(200, json={"items": []}))

    client.get_stream_contents("s", count=20, unread_only=True, ranked="newest", continuation="c1")

    assert dict(route.calls.last.request.url.params) == {
        "streamId": "s",
        "count": "20",
        "unreadOnly": "true",
        "ranked": "newest",
        "continuation": "c1",
    }


@respx.mock
def test_iter_entries_follows_continuation_until_exhausted(client):
    pages = [
        httpx.Response(200, json={"items": [{"id": "e1"}, {"id": "e2"}], "continuation": "c1"}),
        httpx.Response(200, json={"items": [{"id": "e3"}]}),
    ]
    route = respx.get(f"{BASE}/streams/contents").mock(side_effect=pages)

    ids = [entry.id for entry in client.iter_entries("s", page_size=2)]

    assert ids == ["e1", "e2", "e3"]
    assert dict(route.calls[1].request.url.params)["continuation"] == "c1"


@respx.mock
def test_iter_entries_respects_limit_and_stops_early(client):
    route = respx.get(f"{BASE}/streams/contents").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "e1"}, {"id": "e2"}], "continuation": "c1"})
    )

    ids = [entry.id for entry in client.iter_entries("s", limit=2, page_size=50)]

    assert ids == ["e1", "e2"]
    assert route.call_count == 1
    assert dict(route.calls.last.request.url.params)["count"] == "2"


@respx.mock
def test_iter_entries_without_continuation_stops_after_first_page(client):
    respx.get(f"{BASE}/streams/contents").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "e1"}], "continuation": "c1"})
    )

    ids = [entry.id for entry in client.iter_entries("s", follow_continuation=False)]

    assert ids == ["e1"]


@respx.mock
def test_mark_as_read_batches_ids(client):
    route = respx.post(f"{BASE}/markers").mock(return_value=httpx.Response(200, json={}))

    submitted = client.mark_as_read([f"e{i}" for i in range(250)])

    assert submitted == 250
    assert route.call_count == 3
    first = json.loads(route.calls[0].request.read())
    assert first["action"] == "markAsRead"
    assert len(first["entryIds"]) == 100


@respx.mock
def test_keep_unread_uses_keep_unread_action(client):
    route = respx.post(f"{BASE}/markers").mock(return_value=httpx.Response(200, json={}))

    client.keep_unread(["e1"])

    assert json.loads(route.calls.last.request.read()) == {
        "action": "keepUnread",
        "type": "entries",
        "entryIds": ["e1"],
    }


@respx.mock
def test_http_error_is_wrapped(client):
    respx.get(f"{BASE}/collections").mock(
        return_value=httpx.Response(401, text=f"bad token {TOKEN} rejected")
    )

    with pytest.raises(FeedlyError) as error:
        client.get_collections()

    assert error.value.status == 401
    assert TOKEN not in error.value.body
    assert TOKEN not in str(error.value)
    assert error.value.body == "bad token [redacted] rejected"


@respx.mock
def test_transport_error_is_wrapped(client):
    respx.get(f"{BASE}/collections").mock(side_effect=httpx.ConnectError("boom"))

    with pytest.raises(FeedlyError, match="could not reach Feedly"):
        client.get_collections()


@respx.mock
def test_invalid_json_is_wrapped(client):
    respx.get(f"{BASE}/collections").mock(return_value=httpx.Response(200, text="not json"))

    with pytest.raises(FeedlyError):
        client.get_collections()


@pytest.mark.parametrize(
    ("token", "user_id"),
    [("", "u1"), ("  ", "u1"), ("t", ""), ("t", "user/u1")],
)
def test_constructor_validates_credentials(token, user_id):
    with pytest.raises(ValueError):
        FeedlyClient(token, user_id)


def test_all_stream_id(client):
    assert client.all_stream_id == "user/u1/category/global.all"


def test_marker_actions_reject_empty_input(client):
    with pytest.raises(ValueError):
        client.mark_as_read([])
    with pytest.raises(ValueError):
        client.mark_as_read(["e1", " "])


@respx.mock
def test_iter_entries_follows_continuation_past_an_empty_page(client):
    pages = [
        httpx.Response(200, json={"items": [], "continuation": "c1"}),
        httpx.Response(200, json={"items": [{"id": "e1"}]}),
    ]
    respx.get(f"{BASE}/streams/contents").mock(side_effect=pages)

    assert [entry.id for entry in client.iter_entries("s")] == ["e1"]


@respx.mock
def test_iter_entries_stops_on_a_repeated_continuation(client):
    route = respx.get(f"{BASE}/streams/contents").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "e1"}], "continuation": "same"})
    )

    entries = list(client.iter_entries("s", page_size=1))

    assert route.call_count == 2
    assert [entry.id for entry in entries] == ["e1", "e1"]


@respx.mock
def test_iter_entries_honours_the_page_ceiling(client):
    route = respx.get(f"{BASE}/streams/contents").mock(
        side_effect=lambda request: httpx.Response(
            200, json={"items": [{"id": "e"}], "continuation": str(route.call_count)}
        )
    )

    list(client.iter_entries("s", page_size=1, max_pages=3))

    assert route.call_count == 3


@respx.mock
def test_partial_marker_failure_reports_what_was_submitted(client):
    respx.post(f"{BASE}/markers").mock(
        side_effect=[httpx.Response(200, json={}), httpx.Response(500, text="boom")]
    )

    with pytest.raises(PartialMarkerError) as error:
        client.mark_as_read([f"e{i}" for i in range(150)])

    assert error.value.submitted == 100
    assert error.value.status == 500
