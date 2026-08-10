"""HTTP client for the Feedly Cloud API v3.

The client sends the smallest query string that works: browser telemetry parameters (``ct``, ``cv``)
are never added and stream controls appear only when a caller asks for them.
"""

from collections.abc import Iterable, Iterator, Sequence
from typing import Any, Self

import httpx
from pydantic import ValidationError

from .errors import FeedlyError, PartialMarkerError
from .models import Collection, Entry, StreamContents, UnreadCounts

DEFAULT_BASE_URL = "https://api.feedly.com/v3"
MAX_PAGE_SIZE = 1000
MARKER_BATCH_SIZE = 100
MAX_PAGES = 200
REDACTED = "[redacted]"


class FeedlyClient:
    """Authenticated client bound to one Feedly user."""

    def __init__(
        self,
        access_token: str,
        user_id: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not access_token.strip():
            raise ValueError("access_token must not be empty")
        if not user_id.strip() or "/" in user_id:
            raise ValueError("user_id must be a non-empty bare Feedly user id")
        self.user_id = user_id
        self._access_token = access_token
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "User-Agent": "feedly-client",
            },
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._http.close()

    @property
    def all_stream_id(self) -> str:
        """Stream id aggregating every subscription of this user."""
        return f"user/{self.user_id}/category/global.all"

    def get_collections(self) -> tuple[Collection, ...]:
        """Return the user's collections with their feeds."""
        data = self._request("GET", "/collections")
        if not isinstance(data, list):
            raise FeedlyError("unexpected collections response")
        return tuple(self._parse(Collection, item) for item in data if isinstance(item, dict))

    def get_unread_counts(self) -> UnreadCounts:
        """Return unread counters for every stream of the account."""
        data = self._request("GET", "/markers/counts")
        if not isinstance(data, dict):
            raise FeedlyError("unexpected unread counts response")
        return self._parse(UnreadCounts, data)

    def get_stream_contents(
        self,
        stream_id: str,
        *,
        count: int | None = None,
        unread_only: bool | None = None,
        ranked: str | None = None,
        continuation: str | None = None,
    ) -> StreamContents:
        """Return one page of entries from ``stream_id``."""
        if not stream_id:
            raise ValueError("stream_id must not be empty")
        if count is not None and count <= 0:
            raise ValueError("count must be positive")
        params: dict[str, str] = {"streamId": stream_id}
        if count is not None:
            params["count"] = str(min(count, MAX_PAGE_SIZE))
        if unread_only is not None:
            params["unreadOnly"] = "true" if unread_only else "false"
        if ranked is not None:
            params["ranked"] = ranked
        if continuation is not None:
            params["continuation"] = continuation
        data = self._request("GET", "/streams/contents", params=params)
        if not isinstance(data, dict):
            raise FeedlyError("unexpected stream contents response")
        return self._parse(StreamContents, data)

    def iter_entries(
        self,
        stream_id: str,
        *,
        unread_only: bool = True,
        limit: int | None = None,
        page_size: int = 100,
        follow_continuation: bool = True,
        max_pages: int = MAX_PAGES,
    ) -> Iterator[Entry]:
        """Yield entries from ``stream_id``, following continuations when allowed.

        ``limit`` caps the number of yielded entries; ``None`` means "until the stream ends".
        A page can legitimately be empty while a continuation is present (Feedly filtered its whole
        slice away), so paging stops on an exhausted cursor rather than on an empty page.  A cursor
        that repeats itself, or ``max_pages`` requests, also stops the walk instead of looping.
        """
        if page_size <= 0:
            raise ValueError("page_size must be positive")
        if limit is not None and limit <= 0:
            return
        seen = 0
        continuation: str | None = None
        for _ in range(max_pages):
            remaining = None if limit is None else limit - seen
            count = page_size if remaining is None else min(page_size, remaining)
            page = self.get_stream_contents(
                stream_id,
                count=count,
                unread_only=unread_only,
                continuation=continuation,
            )
            for entry in page.items:
                yield entry
                seen += 1
                if limit is not None and seen >= limit:
                    return
            if not page.continuation or page.continuation == continuation or not follow_continuation:
                return
            continuation = page.continuation

    def mark_as_read(self, entry_ids: Iterable[str]) -> int:
        """Mark entries as read and return how many ids Feedly accepted."""
        return self._marker_action("markAsRead", entry_ids)

    def keep_unread(self, entry_ids: Iterable[str]) -> int:
        """Mark entries unread again and return how many ids Feedly accepted."""
        return self._marker_action("keepUnread", entry_ids)

    def _marker_action(self, action: str, entry_ids: Iterable[str]) -> int:
        """Submit ids in batches, reporting how many were accepted before any failure."""
        ids = self._validated_ids(entry_ids)
        done = 0
        for batch in _chunks(ids, MARKER_BATCH_SIZE):
            try:
                self._request(
                    "POST",
                    "/markers",
                    payload={"action": action, "type": "entries", "entryIds": list(batch)},
                )
            except FeedlyError as error:
                raise PartialMarkerError(
                    str(error), submitted=done, status=error.status, body=error.body
                ) from error
            done += len(batch)
        return done

    @staticmethod
    def _validated_ids(entry_ids: Iterable[str]) -> list[str]:
        ids = list(entry_ids)
        if not ids:
            raise ValueError("entry_ids must contain at least one id")
        if any(not entry_id.strip() for entry_id in ids):
            raise ValueError("entry_ids must not contain empty ids")
        return ids

    @staticmethod
    def _parse[T](model: type[T], data: dict[str, Any]) -> T:
        try:
            return model.model_validate(data)  # type: ignore[attr-defined]
        except ValidationError as error:
            raise FeedlyError(f"malformed {model.__name__} in Feedly response") from error

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        try:
            response = self._http.request(method, path, params=params, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise FeedlyError(
                f"Feedly returned HTTP {error.response.status_code}",
                status=error.response.status_code,
                body=self._redact(error.response.text),
            ) from error
        except httpx.HTTPError as error:
            raise FeedlyError(f"could not reach Feedly: {error}") from error
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as error:
            raise FeedlyError("Feedly returned invalid JSON", body=self._redact(response.text)) from error

    def _redact(self, body: str) -> str:
        """Never let the access token travel back out through an error body."""
        return body.replace(self._access_token, REDACTED) if self._access_token in body else body


def _chunks(items: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    """Split ``items`` into consecutive slices of at most ``size`` elements."""
    for start in range(0, len(items), size):
        yield items[start : start + size]
