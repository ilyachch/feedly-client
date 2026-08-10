"""Errors raised by the Feedly API layer."""


class FeedlyError(RuntimeError):
    """Failure to reach Feedly or to understand its response.

    ``status`` is set for HTTP error responses and ``body`` keeps the raw payload so a caller can
    inspect a provider-specific error code. The client redacts the access token from ``body`` before
    it is stored, so an error can be printed safely.
    """

    def __init__(self, message: str, *, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class PartialMarkerError(FeedlyError):
    """A batched marker call failed midway.

    ``submitted`` is the number of ids Feedly accepted before the failure, so a caller can report
    the real effect instead of assuming all or nothing.
    """

    def __init__(self, message: str, *, submitted: int, status: int | None = None, body: str = "") -> None:
        super().__init__(message, status=status, body=body)
        self.submitted = submitted
