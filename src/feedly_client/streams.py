"""Resolution of stream ids and human-readable names.

Collections and feeds are fetched once a day and cached, so listing articles by folder name costs
no extra API call.
"""

from dataclasses import dataclass

from .api import Collection, FeedlyClient
from .cache import StreamInfo, StreamNameCache

KIND_COLLECTION = "collection"
KIND_FEED = "feed"


class StreamNotFound(LookupError):
    """The requested stream name or id is unknown."""


class AmbiguousStream(LookupError):
    """The requested name matches more than one stream."""

    def __init__(self, query: str, candidates: tuple[StreamInfo, ...]) -> None:
        listed = ", ".join(f"{item.label} ({item.id})" for item in candidates)
        super().__init__(f"'{query}' matches several streams: {listed}")
        self.candidates = candidates


def _directory_from_collections(collections: tuple[Collection, ...]) -> tuple[StreamInfo, ...]:
    """Flatten collections and their feeds into one lookup table.

    A feed may live in several collections, so memberships accumulate instead of the first one
    winning; otherwise a per-feed walk would skip the feed in every other folder.
    """
    streams: dict[str, StreamInfo] = {}
    for collection in collections:
        label = collection.label or collection.id
        streams.setdefault(collection.id, StreamInfo(collection.id, label, KIND_COLLECTION))
        for feed in collection.feeds:
            known = streams.get(feed.id)
            memberships = (*known.collections, label) if known else (label,)
            streams[feed.id] = StreamInfo(feed.id, feed.title or feed.id, KIND_FEED, memberships)
    return tuple(streams.values())


@dataclass(frozen=True, slots=True)
class StreamDirectory:
    """Known collections and feeds, addressable by id or by label."""

    streams: tuple[StreamInfo, ...]

    @classmethod
    def load(
        cls, client: FeedlyClient, cache: StreamNameCache, *, refresh: bool = False
    ) -> "StreamDirectory":
        """Return the directory from cache, fetching it from Feedly when stale or forced."""
        if not refresh:
            cached = cache.load()
            if cached is not None:
                return cls(cached)
        streams = _directory_from_collections(client.get_collections())
        cache.save(streams)
        return cls(streams)

    @property
    def collections(self) -> tuple[StreamInfo, ...]:
        return tuple(stream for stream in self.streams if stream.kind == KIND_COLLECTION)

    def by_id(self, stream_id: str) -> StreamInfo | None:
        """Return the stream with this exact id, if it is known."""
        for stream in self.streams:
            if stream.id == stream_id:
                return stream
        return None

    def label_for(self, stream_id: str) -> str:
        """Return a display name for ``stream_id``, falling back to the id itself."""
        stream = self.by_id(stream_id)
        return stream.label if stream else stream_id

    def feeds_of(self, collection_label: str) -> tuple[StreamInfo, ...]:
        """Return the feeds that belong to a collection."""
        return tuple(stream for stream in self.streams if collection_label in stream.collections)

    def resolve(self, query: str) -> StreamInfo:
        """Resolve an id or a human label to a single stream.

        Raises:
            StreamNotFound: nothing matches the query.
            AmbiguousStream: several streams share the same label.
        """
        query = query.strip()
        if not query:
            raise StreamNotFound("empty stream reference")
        exact_id = self.by_id(query)
        if exact_id is not None:
            return exact_id
        folded = query.casefold()
        matches = tuple(stream for stream in self.streams if stream.label.casefold() == folded)
        if not matches:
            matches = tuple(stream for stream in self.streams if folded in stream.label.casefold())
        if not matches:
            if query.startswith(("feed/", "user/")):
                return StreamInfo(query, query, KIND_FEED)
            raise StreamNotFound(f"no collection or feed matches '{query}'")
        if len(matches) > 1:
            raise AmbiguousStream(query, matches)
        return matches[0]
