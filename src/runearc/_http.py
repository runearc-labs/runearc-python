"""What the sync and async clients share: building requests, retrying, reading responses."""

from __future__ import annotations

import json
import os
import random
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Union, cast
from urllib.parse import quote

import httpx

from .errors import RunearcError
from .types import BatchItemError, TimeInput

VERSION = "0.1.0"
DEFAULT_BASE_URL = "https://api.runearc.com"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_RETRIES = 2
MAX_RETRY_WAIT = 60.0

QueryValue = Union[str, int, float, bool, datetime, None]


@dataclass
class Call:
    method: str
    path: str
    query: Mapping[str, QueryValue] = field(default_factory=dict)
    body: Any = None
    headers: Mapping[str, str] = field(default_factory=dict)
    sends_mail: bool = False
    """Sends mail: gets an automatic Idempotency-Key."""
    idempotent: bool = False
    """A POST that is safe to repeat (verify, cancel, mark read)."""
    text: bool = False
    idempotency_key: Optional[str] = None
    timeout: Optional[float] = None


@dataclass
class Prepared:
    url: str
    headers: Dict[str, str]
    content: Optional[bytes]
    idempotency_key: Optional[str]
    repeatable: bool
    timeout: float


def seg(value: str) -> str:
    """One URL path segment."""
    return quote(value, safe="")


def iso(value: TimeInput) -> str:
    """An RFC 3339 string for a ``datetime`` (UTC, milliseconds); strings pass through."""
    if isinstance(value, datetime):
        utc = value.astimezone(timezone.utc)
        return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"
    return value


def email_body(email: Mapping[str, Any]) -> Dict[str, Any]:
    """A send request with ``scheduled_at`` as the API's string."""
    body = dict(email)
    if body.get("scheduled_at") is not None:
        body["scheduled_at"] = iso(body["scheduled_at"])
    return body


def retry_after_seconds(value: Optional[str]) -> Optional[float]:
    """Seconds to wait from a ``Retry-After`` header (seconds or an HTTP date)."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        at = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return max(0.0, (at - datetime.now(timezone.utc)).total_seconds())


def backoff_seconds(attempt: int) -> float:
    """Exponential backoff with jitter: 0.25-0.5s, 0.5-1s, ... up to 4-8s."""
    cap = min(8.0, 0.5 * 2.0**attempt)
    return cap / 2 + random.random() * (cap / 2)


def env(name: str) -> Optional[str]:
    return os.environ.get(name) or None


def normalize_base_url(base: str) -> str:
    base = base.rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


def missing_api_key() -> RunearcError:
    return RunearcError(
        status=0,
        code="missing_api_key",
        message="no Runearc API key was given",
        fix="Pass it as Runearc('mb_...') or set RUNEARC_API_KEY",
    )


class Settings:
    """Everything both clients read from their constructor arguments and the environment."""

    def __init__(
        self,
        api_key: Optional[str],
        base_url: Optional[str],
        max_retries: int,
        timeout: float,
        headers: Optional[Mapping[str, str]],
    ) -> None:
        key = api_key or env("RUNEARC_API_KEY")
        if not key:
            raise missing_api_key()
        self.api_key = key
        self.base_url = normalize_base_url(base_url or env("RUNEARC_BASE_URL") or DEFAULT_BASE_URL)
        self.max_retries = max(0, max_retries)
        self.timeout = timeout
        self.headers = dict(headers or {})

    def prepare(self, call: Call) -> Prepared:
        params: Dict[str, str] = {}
        for k, v in call.query.items():
            if v is None:
                continue
            if isinstance(v, datetime):
                params[k] = iso(v)
            elif isinstance(v, bool):
                params[k] = "true" if v else "false"
            else:
                params[k] = str(v)
        url = self.base_url + call.path
        if params:
            url = str(httpx.URL(url, params=params))
        headers = {
            "Accept": "application/json",
            "User-Agent": f"runearc-python/{VERSION}",
            **self.headers,
            **call.headers,
            "Authorization": f"Bearer {self.api_key}",
        }
        # One key for every attempt, so a retry returns the first result instead of sending again.
        key = call.idempotency_key or (str(uuid.uuid4()) if call.sends_mail else None)
        if key:
            headers["Idempotency-Key"] = key
        content: Optional[bytes] = None
        if call.body is not None:
            headers["Content-Type"] = "application/json"
            content = json.dumps(call.body, separators=(",", ":")).encode("utf-8")
        # Repeating must not create a second resource: GET, DELETE and PATCH are
        # idempotent, and a POST is when it carries an Idempotency-Key.
        repeatable = call.method != "POST" or key is not None or call.idempotent
        return Prepared(
            url=url,
            headers=headers,
            content=content,
            idempotency_key=key,
            repeatable=repeatable,
            timeout=self.timeout if call.timeout is None else call.timeout,
        )

    def transport_error(self, exc: httpx.HTTPError, prepared: Prepared) -> RunearcError:
        timed_out = isinstance(exc, httpx.TimeoutException)
        if prepared.idempotency_key:
            fix = (
                f"Retry with the same idempotency_key ({prepared.idempotency_key}) "
                "so the email is not sent twice"
            )
        else:
            fix = "Check your network and base_url, then retry"
        if timed_out:
            message = f"no response from {self.base_url} within {prepared.timeout:g} s"
        else:
            message = f"could not reach {self.base_url}: {exc}"
        return RunearcError(
            status=0,
            code="timeout" if timed_out else "network_error",
            message=message,
            fix=fix,
        )

    def retry_delay(
        self, response: httpx.Response, prepared: Prepared, attempt: int
    ) -> Optional[float]:
        """Seconds to wait before another attempt, or ``None`` when the response is final."""
        if attempt >= self.max_retries:
            return None
        status = response.status_code
        if status == 429 or (status >= 500 and prepared.repeatable):
            wait = retry_after_seconds(response.headers.get("retry-after"))
            return min(backoff_seconds(attempt) if wait is None else wait, MAX_RETRY_WAIT)
        return None


def parse_success(response: httpx.Response, text: bool) -> Any:
    if text:
        return response.text
    raw = response.text
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise RunearcError(
            status=response.status_code,
            code="invalid_response",
            message="the API sent a response that is not JSON",
            fix="Check that base_url points at the Runearc API",
        ) from exc


def parse_error(response: httpx.Response) -> RunearcError:
    text = response.text
    detail: Dict[str, Any] = {}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
            detail = parsed["error"]
    except ValueError:
        pass
    wait = retry_after_seconds(response.headers.get("retry-after"))
    errors = detail.get("errors")
    return RunearcError(
        status=response.status_code,
        code=str(detail.get("code") or f"http_{response.status_code}"),
        message=str(
            detail.get("message")
            or text[:500]
            or response.reason_phrase
            or f"HTTP {response.status_code}"
        ),
        fix=detail.get("fix"),
        errors=cast("List[BatchItemError]", errors) if isinstance(errors, list) else None,
        retry_after=None if wait is None else int(-(-wait // 1)),
    )


def wait_params(
    from_: Optional[str],
    subject: Optional[str],
    to: Optional[str],
    timeout: int,
    after: Optional[TimeInput],
) -> Dict[str, QueryValue]:
    return {
        "from": from_,
        "subject": subject,
        "to": to,
        "timeout": timeout,
        "after": None if after is None else iso(after),
    }
