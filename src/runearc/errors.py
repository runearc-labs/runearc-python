"""The errors the SDK raises."""

from __future__ import annotations

from typing import List, Optional

from .types import BatchItemError

_KINDS = {
    400: "invalid",
    401: "unauthorized",
    402: "limit_reached",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    429: "rate_limited",
    503: "unavailable",
}


def kind_for_status(status: int) -> str:
    """The API's error kind for an HTTP status.

    ``client`` is a failure before any response arrived. Other 5xx are
    ``internal``; other 4xx are ``invalid``.
    """
    if status == 0:
        return "client"
    known = _KINDS.get(status)
    if known is not None:
        return known
    return "internal" if status >= 500 else "invalid"


class RunearcError(Exception):
    """Every failure the SDK reports.

    API errors carry the API's ``code``, ``message`` and ``fix``. Failures before
    a response arrives use ``status == 0`` and the codes ``network_error``,
    ``timeout``, ``invalid_response`` or ``missing_api_key``.
    """

    status: int
    """The HTTP status, or 0 when no response arrived."""
    code: str
    """A stable machine readable code, such as ``invalid_from`` or ``rate_limited``."""
    kind: str
    """The error class: ``invalid``, ``unauthorized``, ``forbidden``, ``not_found``,
    ``conflict``, ``limit_reached``, ``rate_limited``, ``unavailable``, ``internal``
    or ``client`` (no response). Derived from the HTTP status."""
    message: str
    fix: Optional[str]
    """What to do about it, when there is something you can do."""
    errors: Optional[List[BatchItemError]]
    """``invalid_batch`` only: every refused item of the batch."""
    retry_after: Optional[int]
    """Seconds the API asked us to wait (``Retry-After``), when it sent one."""

    def __init__(
        self,
        *,
        status: int,
        code: str,
        message: str,
        fix: Optional[str] = None,
        errors: Optional[List[BatchItemError]] = None,
        retry_after: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.kind = kind_for_status(status)
        self.message = message
        self.fix = fix
        self.errors = errors
        self.retry_after = retry_after

    def __str__(self) -> str:
        return self.message

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(status={self.status}, code={self.code!r}, "
            f"message={self.message!r})"
        )

    def to_dict(self) -> dict[str, object]:
        """The error as JSON friendly data."""
        data: dict[str, object] = {
            "name": type(self).__name__,
            "status": self.status,
            "code": self.code,
            "kind": self.kind,
            "message": self.message,
            "fix": self.fix,
        }
        if self.errors is not None:
            data["errors"] = self.errors
        return data


class WebhookVerificationError(RunearcError):
    """Raised by ``verify_webhook`` when a payload is not a genuine, fresh Runearc webhook."""

    def __init__(self, code: str, message: str, fix: Optional[str] = None) -> None:
        super().__init__(status=0, code=code, message=message, fix=fix)
