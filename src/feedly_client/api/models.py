"""Pydantic models for the Feedly Cloud API v3 responses.

Only the fields the utility actually uses are modelled. ``extra="allow"`` keeps every other field
Feedly sends, so a change on their side never breaks parsing.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    """Base model: camelCase aliases, tolerant of unknown fields."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="allow",
        frozen=True,
    )


def _from_millis(value: int | None) -> datetime | None:
    """Convert a Feedly millisecond timestamp to an aware UTC datetime."""
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC)


class Link(ApiModel):
    """A hyperlink from an entry's ``canonical`` or ``alternate`` list."""

    href: str
    type: str | None = None


class Content(ApiModel):
    """HTML or text body of an entry's ``summary`` or ``content`` field."""

    content: str = ""
    direction: str | None = None


class Origin(ApiModel):
    """The feed an entry came from."""

    stream_id: str
    title: str | None = None
    html_url: str | None = None


class Category(ApiModel):
    """A collection an entry belongs to."""

    id: str
    label: str | None = None


class Entry(ApiModel):
    """One article returned by ``/streams/contents``."""

    id: str
    title: str | None = None
    author: str | None = None
    published: int | None = None
    updated: int | None = None
    crawled: int | None = None
    unread: bool | None = None
    canonical: tuple[Link, ...] = ()
    alternate: tuple[Link, ...] = ()
    canonical_url: str | None = None
    summary: Content | None = None
    content: Content | None = None
    origin: Origin | None = None
    categories: tuple[Category, ...] = ()
    keywords: tuple[str, ...] = ()

    @property
    def url(self) -> str | None:
        """Best available article URL."""
        if self.canonical_url:
            return self.canonical_url
        for links in (self.canonical, self.alternate):
            for link in links:
                if link.href:
                    return link.href
        return None

    @property
    def source_title(self) -> str | None:
        """Human-readable feed title, when Feedly supplied one."""
        return self.origin.title if self.origin else None

    @property
    def source_id(self) -> str | None:
        """Stream id of the feed the entry came from."""
        return self.origin.stream_id if self.origin else None

    @property
    def published_at(self) -> datetime | None:
        """Publication time, falling back to the crawl time."""
        return _from_millis(self.published) or _from_millis(self.crawled)

    @property
    def body_html(self) -> str:
        """Richest available body text, preferring the full content over the summary."""
        for candidate in (self.content, self.summary):
            if candidate and candidate.content:
                return candidate.content
        return ""


class StreamContents(ApiModel):
    """One page of a stream plus its continuation cursor."""

    id: str | None = None
    updated: int | None = None
    continuation: str | None = None
    items: tuple[Entry, ...] = ()


class Feed(ApiModel):
    """A feed subscribed inside a collection."""

    id: str
    title: str | None = None
    website: str | None = None


class Collection(ApiModel):
    """A user collection (folder) and its feeds."""

    id: str
    label: str | None = None
    description: str | None = None
    feeds: tuple[Feed, ...] = ()


class UnreadCount(ApiModel):
    """Unread counter for a single stream."""

    id: str
    count: int
    updated: int | None = None


class UnreadCounts(ApiModel):
    """The ``/markers/counts`` response."""

    unread_counts: tuple[UnreadCount, ...] = Field(default=(), alias="unreadcounts")
    updated: int | None = None
