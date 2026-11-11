from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from typing import Dict, List, Optional, Tuple

import pytest

from helpers import Stack, eventually, unique
from runearc import AsyncRunearc, Runearc, WebhookVerificationError, verify_webhook
from servers import Request, Server


def sign(secret: str, msg_id: str, ts: int, body: str) -> str:
    """Computed independently, the way runearc_core::webhooks::sign does it."""
    key = base64.b64decode(secret.removeprefix("whsec_"))
    digest = hmac.new(key, f"{msg_id}.{ts}.{body}".encode(), hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode()


SECRET = "whsec_" + base64.b64encode(os.urandom(32)).decode()
BODY = json.dumps(
    {
        "id": "evt_1",
        "type": "message.delivered",
        "timestamp": "2026-10-14T10:00:00.000Z",
        "data": {"email_id": "eml_1"},
    }
)
NOW = int(time.time())
HEADERS = {
    "webhook-id": "evt_1",
    "webhook-timestamp": str(NOW),
    "webhook-signature": sign(SECRET, "evt_1", NOW, BODY),
}


def test_matches_the_standard_webhooks_test_vector() -> None:
    event = verify_webhook(
        '{"test": 2432232314}',
        {
            "webhook-id": "msg_p5jXN8AQM9LWM0D4loKWxJek",
            "webhook-timestamp": "1614265330",
            "webhook-signature": "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE=",
        },
        "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw",
        now=1614265330,
    )
    assert dict(event) == {"test": 2432232314}


def test_accepts_str_bytes_any_header_case_and_several_signatures() -> None:
    event = verify_webhook(BODY, HEADERS, SECRET)
    assert event["type"] == "message.delivered"
    upper = {k.upper(): v for k, v in HEADERS.items()}
    assert verify_webhook(BODY.encode(), upper, SECRET) == event
    multi = {**HEADERS, "webhook-signature": f"v1,AAAA {HEADERS['webhook-signature']}"}
    assert verify_webhook(BODY, multi, SECRET) == event
    listed: Dict[str, List[str]] = {**{k: [v] for k, v in HEADERS.items()}}
    assert verify_webhook(BODY, listed, SECRET) == event


def test_client_webhooks_verify_is_the_same_function() -> None:
    mb = Runearc("mb_test")
    assert mb.webhooks.verify(BODY, HEADERS, SECRET) == verify_webhook(BODY, HEADERS, SECRET)


def test_async_client_webhooks_verify_is_the_same_function() -> None:
    mb = AsyncRunearc("mb_test")
    assert mb.webhooks.verify(BODY, HEADERS, SECRET) == verify_webhook(BODY, HEADERS, SECRET)


def test_refuses_tampered_bodies_and_wrong_secrets() -> None:
    with pytest.raises(WebhookVerificationError) as tampered:
        verify_webhook(BODY.replace("eml_1", "eml_2"), HEADERS, SECRET)
    assert tampered.value.code == "invalid_signature"
    assert tampered.value.fix
    other = "whsec_" + base64.b64encode(os.urandom(32)).decode()
    with pytest.raises(WebhookVerificationError) as wrong:
        verify_webhook(BODY, HEADERS, other)
    assert wrong.value.code == "invalid_signature"


def test_refuses_stale_future_and_malformed_timestamps() -> None:
    old = NOW - 301
    stale = {
        **HEADERS,
        "webhook-timestamp": str(old),
        "webhook-signature": sign(SECRET, "evt_1", old, BODY),
    }
    with pytest.raises(WebhookVerificationError) as info:
        verify_webhook(BODY, stale, SECRET, now=NOW)
    assert info.value.code == "timestamp_out_of_range"
    ahead = NOW + 301
    future = {
        **HEADERS,
        "webhook-timestamp": str(ahead),
        "webhook-signature": sign(SECRET, "evt_1", ahead, BODY),
    }
    with pytest.raises(WebhookVerificationError):
        verify_webhook(BODY, future, SECRET, now=NOW)
    # A wider tolerance accepts the stale one.
    assert verify_webhook(BODY, stale, SECRET, tolerance_seconds=600, now=NOW)["id"] == "evt_1"
    with pytest.raises(WebhookVerificationError) as bad:
        verify_webhook(BODY, {**HEADERS, "webhook-timestamp": "yesterday"}, SECRET)
    assert bad.value.code == "invalid_timestamp"


def test_refuses_missing_headers_and_bad_secrets_and_bodies() -> None:
    with pytest.raises(WebhookVerificationError) as missing:
        verify_webhook(BODY, {"webhook-id": "evt_1"}, SECRET)
    assert missing.value.code == "missing_headers"
    with pytest.raises(WebhookVerificationError) as secret:
        verify_webhook(BODY, HEADERS, "whsec_not base64!!")
    assert secret.value.code == "invalid_secret"
    not_json = "not json"
    headers = {**HEADERS, "webhook-signature": sign(SECRET, "evt_1", NOW, not_json)}
    with pytest.raises(WebhookVerificationError) as payload:
        verify_webhook(not_json, headers, SECRET)
    assert payload.value.code == "invalid_payload"


def test_verification_errors_are_runearc_errors() -> None:
    from runearc import RunearcError

    with pytest.raises(RunearcError) as info:
        verify_webhook(BODY, {}, SECRET)
    assert info.value.status == 0
    assert info.value.kind == "client"


def test_real_signed_delivery_is_verified_listed_replayed_and_deleted(
    mb: Runearc, stack: Stack
) -> None:
    received: List[Tuple[Dict[str, str], str]] = []
    lock = threading.Lock()

    def receive(req: Request) -> None:
        with lock:
            received.append((dict(req.headers), req.body.decode()))
        req.reply(200, raw=b"ok")

    def find(email_id: str) -> Optional[Tuple[Dict[str, str], str]]:
        with lock:
            return next((r for r in received if email_id in r[1]), None)

    server = Server(receive)
    try:
        hook = mb.webhooks.create(
            {"url": f"{server.url}/hook", "event_types": ["message.delivered"]}
        )
        assert hook["id"]
        assert hook["secret"].startswith("whsec_")
        assert any(w["id"] == hook["id"] for w in mb.webhooks.list()["data"])

        email_id = mb.emails.send(
            {
                "from": stack.sender(),
                "to": stack.test_address(),
                "subject": unique("hook"),
                "text": "hi",
            }
        )["id"]
        headers, body = eventually(lambda: find(email_id), 30)
        event = mb.webhooks.verify(body, headers, hook["secret"])
        assert event["type"] == "message.delivered"
        assert event["id"] == headers["webhook-id"]
        assert email_id in json.dumps(event["data"])

        def settled() -> Optional[List[str]]:
            data = mb.webhooks.deliveries(hook["id"], limit=10)["data"]
            done = bool(data) and all(d["status"] != "pending" for d in data)
            return [d["id"] for d in data] if done else None

        delivery_id = eventually(settled)[0]
        before = len(received)
        replay = mb.webhooks.replay(hook["id"], delivery_id)
        assert replay["replay_of"] == delivery_id
        eventually(lambda: True if len(received) > before else None, 30)
        again_headers, again_body = received[-1]
        assert (
            verify_webhook(again_body, again_headers, hook["secret"])["type"] == "message.delivered"
        )

        mb.webhooks.delete(hook["id"])
        assert not any(w["id"] == hook["id"] for w in mb.webhooks.list()["data"])
    finally:
        server.close()
