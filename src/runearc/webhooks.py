"""Standard Webhooks verification (https://www.standardwebhooks.com) with the standard library only.

Mirrors ``runearc_core::webhooks::sign``::

    signature = "v1," + base64(HMAC-SHA256(base64decode(secret without "whsec_"),
                                           "{webhook-id}.{webhook-timestamp}.{body}"))
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from typing import Any, Optional, Union, cast

from .errors import WebhookVerificationError
from .types import WebhookEvent

WEBHOOK_TOLERANCE_SECONDS = 5 * 60
"""Signatures older or newer than this are refused (replay protection)."""

_PREFIX = "whsec_"


def _header(headers: Mapping[str, Any], name: str) -> Optional[str]:
    for key, value in headers.items():
        if key.lower() == name:
            if isinstance(value, (list, tuple)):
                return " ".join(str(v) for v in value)
            return None if value is None else str(value)
    return None


def verify_webhook(
    payload: Union[str, bytes],
    headers: Mapping[str, Any],
    secret: str,
    *,
    tolerance_seconds: int = WEBHOOK_TOLERANCE_SECONDS,
    now: Optional[float] = None,
) -> WebhookEvent:
    """Check that a webhook request came from Runearc and is fresh, then return the parsed event.

    ``payload`` is the raw request body, exactly as received: do not parse it and
    serialize it again. ``headers`` is any mapping of the request headers (any
    case, such as Flask's ``request.headers`` or Starlette's ``request.headers``).
    ``secret`` is the endpoint's ``whsec_...`` secret from ``webhooks.create``.
    ``now`` is for tests: the current time in seconds since the epoch.

    Raises ``WebhookVerificationError`` when the request is not genuine.
    """
    msg_id = _header(headers, "webhook-id")
    ts = _header(headers, "webhook-timestamp")
    sigs = _header(headers, "webhook-signature")
    if not msg_id or not ts or not sigs:
        raise WebhookVerificationError(
            "missing_headers",
            "the webhook-id, webhook-timestamp and webhook-signature headers are required",
            "Pass the request headers unchanged; check that a proxy does not strip them",
        )
    ts = ts.strip()
    if not (ts.isascii() and ts.isdigit()):
        raise WebhookVerificationError(
            "invalid_timestamp", "webhook-timestamp is not a number of seconds"
        )
    current = time.time() if now is None else now
    if abs(current - int(ts)) > tolerance_seconds:
        raise WebhookVerificationError(
            "timestamp_out_of_range",
            f"the webhook timestamp is more than {tolerance_seconds} seconds from now",
            "Check your server clock; old deliveries are refused to stop replays",
        )

    secret = secret.strip()
    if secret.startswith(_PREFIX):
        secret = secret[len(_PREFIX) :]
    try:
        key = base64.b64decode(secret, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise WebhookVerificationError(
            "invalid_secret",
            "the webhook secret is not valid base64",
            "Use the secret returned by webhooks.create, which starts with whsec_",
        ) from exc

    body = payload if isinstance(payload, bytes) else payload.encode("utf-8")
    signed = msg_id.encode("utf-8") + b"." + ts.encode("ascii") + b"." + body
    expected = hmac.new(key, signed, hashlib.sha256).digest()

    for part in sigs.split(" "):
        version, _, sig = part.partition(",")
        if version != "v1" or not sig:
            continue
        try:
            candidate = base64.b64decode(sig, validate=True)
        except (binascii.Error, ValueError):
            continue
        if hmac.compare_digest(candidate, expected):
            try:
                return cast(WebhookEvent, json.loads(body))
            except ValueError as exc:
                raise WebhookVerificationError(
                    "invalid_payload", "the webhook body is not JSON"
                ) from exc
    raise WebhookVerificationError(
        "invalid_signature",
        "no webhook-signature matches the payload",
        "Verify the raw body (not JSON parsed and serialized again) "
        "with this endpoint's whsec_ secret",
    )
