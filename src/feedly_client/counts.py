"""Unread counters grouped by collection, with a fetch-strategy hint.

The hint lets the agent decide, without spending tokens, whether it can read everything at once or
should walk collections and feeds one by one.
"""

from typing import Any

from .api import UnreadCounts
from .streams import StreamDirectory

STRATEGY_ALL = "all"
STRATEGY_PER_COLLECTION = "per_collection"
STRATEGY_COLLECTION = "collection"
STRATEGY_PER_FEED = "per_feed"

UNCATEGORIZED = "uncategorized"


def build_counts_report(
    counts: UnreadCounts,
    directory: StreamDirectory,
    *,
    all_stream_id: str,
    threshold: int,
    include_empty: bool = False,
) -> dict[str, Any]:
    """Join unread counters with stream names and recommend how to fetch them."""
    if threshold <= 0:
        raise ValueError("threshold must be positive")
    by_id = {counter.id: counter.count for counter in counts.unread_counts}
    known_feeds = {stream.id for stream in directory.streams if stream.kind == "feed"}

    collections: list[dict[str, Any]] = []
    for collection in directory.collections:
        feeds = [
            {"id": feed.id, "title": feed.label, "unread": by_id.get(feed.id, 0)}
            for feed in directory.feeds_of(collection.label)
        ]
        unread = by_id.get(collection.id, sum(feed["unread"] for feed in feeds))
        if not include_empty:
            feeds = [feed for feed in feeds if feed["unread"] > 0]
            if unread == 0:
                continue
        collections.append(
            {
                "id": collection.id,
                "label": collection.label,
                "unread": unread,
                "strategy": STRATEGY_COLLECTION if unread <= threshold else STRATEGY_PER_FEED,
                "feeds": sorted(feeds, key=lambda feed: feed["unread"], reverse=True),
            }
        )

    orphans = [
        {"id": stream_id, "title": directory.label_for(stream_id), "unread": count}
        for stream_id, count in by_id.items()
        if stream_id.startswith("feed/") and stream_id not in known_feeds and (count > 0 or include_empty)
    ]

    total = by_id.get(all_stream_id)
    if total is None:
        # Feeds are the leaves of the account, so summing them counts every article exactly once
        # even when the cached collection directory is stale.
        total = sum(count for stream_id, count in by_id.items() if stream_id.startswith("feed/"))

    return {
        "total_unread": total,
        "threshold": threshold,
        "strategy": STRATEGY_ALL if total <= threshold else STRATEGY_PER_COLLECTION,
        "collections": sorted(collections, key=lambda item: item["unread"], reverse=True),
        UNCATEGORIZED: sorted(orphans, key=lambda item: item["unread"], reverse=True),
    }
