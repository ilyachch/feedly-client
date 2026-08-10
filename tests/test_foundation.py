from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from feedly_client.cache import EntryMeta, EntryMetaCache, StreamInfo, StreamNameCache
from feedly_client.config import ConfigError, load_settings
from feedly_client.paths import DataPaths
from feedly_client.text import html_to_text, snippet, truncate


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setenv("FEEDLY_ENV_FILE", str(path))
    return path


def test_settings_from_env_file(env_file, monkeypatch):
    monkeypatch.delenv("FEEDLY_API_KEY", raising=False)
    monkeypatch.delenv("FEEDLY_USER_ID", raising=False)
    env_file.write_text("FEEDLY_API_KEY=file-token\nFEEDLY_USER_ID=u1\n", encoding="utf-8")

    settings = load_settings({})

    assert settings.api_key == "file-token"
    assert settings.user_id == "u1"
    assert settings.data_dir is None


def test_real_env_wins_over_file(env_file):
    env_file.write_text("FEEDLY_API_KEY=file-token\nFEEDLY_USER_ID=u1\n", encoding="utf-8")

    settings = load_settings({"FEEDLY_API_KEY": "env-token", "FEEDLY_DATA_DIR": "~/data"})

    assert settings.api_key == "env-token"
    assert settings.user_id == "u1"
    assert settings.data_dir == Path("~/data").expanduser()


def test_missing_credentials_are_reported_together(env_file):
    with pytest.raises(ConfigError, match="FEEDLY_API_KEY and FEEDLY_USER_ID"):
        load_settings({})


def test_user_id_must_be_bare(env_file):
    with pytest.raises(ConfigError, match="bare user id"):
        load_settings({"FEEDLY_API_KEY": "t", "FEEDLY_USER_ID": "user/u1"})


def test_data_paths_layout(tmp_path):
    paths = DataPaths.resolve(tmp_path / "data").ensure()

    assert paths.rules_file == tmp_path / "data" / "rules.md"
    assert paths.logs_dir.is_dir()
    assert paths.cache_dir.is_dir()
    assert paths.as_dict()["data_dir"] == str(tmp_path / "data")


def test_default_data_paths_are_outside_the_repo():
    root = DataPaths.resolve().root

    assert root.is_absolute()
    assert Path.cwd() not in root.parents


def test_stream_name_cache_roundtrip_and_expiry(tmp_path):
    cache = StreamNameCache(tmp_path / "streams.json")
    streams = (
        StreamInfo(id="user/u1/category/News", label="News", kind="collection"),
        StreamInfo(id="feed/x", label="BBC", kind="feed", collections=("News",)),
    )
    cache.save(streams)

    assert cache.load() == streams

    stale = StreamNameCache(tmp_path / "streams.json", ttl=timedelta(seconds=-1))
    assert stale.load() is None

    cache.clear()
    assert cache.load() is None


def test_stream_name_cache_survives_corruption(tmp_path):
    path = tmp_path / "streams.json"
    path.write_text("{not json", encoding="utf-8")

    assert StreamNameCache(path).load() is None


def test_entry_meta_cache_merges_and_expires(tmp_path):
    path = tmp_path / "entries.json"
    cache = EntryMetaCache(path)
    cache.remember([EntryMeta(id="e1", title="One", source="BBC", url="https://x", stream="News")])
    cache.remember([EntryMeta(id="e2", title="Two")])

    assert cache.get("e1").title == "One"
    assert cache.get("e2").title == "Two"
    assert cache.get("missing") is None

    expired = EntryMetaCache(path, ttl=timedelta(seconds=-1))
    assert expired.get("e1") is None


def test_entry_meta_cache_is_bounded(tmp_path):
    cache = EntryMetaCache(tmp_path / "entries.json", limit=10)
    cache.remember([EntryMeta(id=f"e{i}") for i in range(25)])

    stored = [i for i in range(25) if cache.get(f"e{i}") is not None]
    assert len(stored) == 10


def test_entry_meta_cache_ignores_empty_input(tmp_path):
    path = tmp_path / "entries.json"
    EntryMetaCache(path).remember([])

    assert not path.exists()


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("<p>Hello <b>world</b></p>", "Hello world"),
        ("<script>alert(1)</script>Text", "Text"),
        ("<div>a</div><div>b</div>", "a b"),
        ("Caf&eacute; &amp; bar", "Café & bar"),
        ("line1<br>line2", "line1 line2"),
        ("", ""),
    ],
)
def test_html_to_text(html, expected):
    assert html_to_text(html) == expected


def test_truncate_cuts_on_word_boundary():
    assert truncate("one two three four", 12) == "one two…"
    assert truncate("short", 50) == "short"
    assert truncate("text", 0) == ""
    assert truncate("aaaaaaaaaaaa", 5) == "aaaaa…"


def test_snippet_combines_conversion_and_truncation():
    assert snippet("<p>Hello   big <i>world</i> of news</p>", 11) == "Hello big…"


def test_datetime_helpers_are_utc():
    assert datetime.now(UTC).tzinfo is UTC


def test_entry_meta_cache_drops_records_without_a_usable_timestamp(tmp_path):
    path = tmp_path / "entries.json"
    path.write_text('{"entries": {"e1": {"title": "One"}, "e2": {"seen_at": "nonsense"}}}', encoding="utf-8")

    cache = EntryMetaCache(path)

    assert cache.get("e1") is None
    assert cache.get("e2") is None


def test_entry_meta_cache_get_many_reads_the_file_once(tmp_path, monkeypatch):
    cache = EntryMetaCache(tmp_path / "entries.json")
    cache.remember([EntryMeta(id="e1", title="One"), EntryMeta(id="e2", title="Two")])

    import feedly_client.cache as cache_module

    reads = 0
    original = cache_module._read_json

    def counting_read(path):
        nonlocal reads
        reads += 1
        return original(path)

    monkeypatch.setattr(cache_module, "_read_json", counting_read)
    found = cache.get_many(["e1", "e2", "missing"])

    assert reads == 1
    assert set(found) == {"e1", "e2"}


def test_empty_stream_cache_is_treated_as_a_miss(tmp_path):
    cache = StreamNameCache(tmp_path / "streams.json")
    cache.save(())

    assert cache.load() is None


def test_relative_data_dir_is_made_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    root = DataPaths.resolve(Path("data")).root

    assert root == tmp_path / "data"
    assert root.is_absolute()


def test_html_to_text_decodes_entities():
    assert html_to_text("Android&nbsp;&amp; iOS\u00a0released") == "Android & iOS released"


def test_truncate_does_not_break_on_entities():
    assert snippet("a&nbsp;b&nbsp;c&nbsp;d", 5) == "a b…"
