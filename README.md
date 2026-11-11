# runearc

Send email and give your agents an inbox, from Python.

```sh
pip install runearc
```

```python
import os
from runearc import Runearc

runearc = Runearc(os.environ["RUNEARC_API_KEY"])
sent = runearc.emails.send(
    {
        "from": "Acme <noreply@acme.com>",
        "to": "bob@example.com",
        "subject": "Welcome to Acme",
        "html": "<p>Thanks for signing up.</p>",
    }
)
```

See [runearc.com/integrations/python](https://runearc.com/integrations/python) for the guide and more examples.

The official Python SDK for [Runearc](https://runearc.com): send, batch and schedule email, create inboxes your agents can read, manage domains and verify webhooks.

One runtime dependency ([httpx](https://www.python-httpx.org)). Webhook verification uses only the standard library. Python 3.9 or later, fully typed (`py.typed`), with a synchronous `Runearc` client and an asynchronous `AsyncRunearc` client.

## Install

```sh
pip install runearc
```

## Quick start

```python
import os
from runearc import Runearc

runearc = Runearc(os.environ["RUNEARC_API_KEY"])

sent = runearc.emails.send(
    {
        "from": "Acme <noreply@acme.com>",
        "to": "bob@example.com",
        "subject": "Welcome to Acme",
        "html": "<p>Thanks for signing up.</p>",
    }
)
print(sent["id"])
```

`Runearc()` with no key reads `RUNEARC_API_KEY`. Constructor arguments (all keyword-only except the key):

| Argument | Default | |
|---|---|---|
| `base_url` | `RUNEARC_BASE_URL`, else `https://api.runearc.com` | A trailing `/v1` is fine. |
| `max_retries` | `2` | Retries after the first attempt (see [Retries](#retries-and-idempotency)). |
| `timeout` | `60.0` | Seconds, per attempt. |
| `headers` | none | Sent with every request, such as `Runearc-Project`. |
| `http_client` | a new `httpx.Client` | Bring your own. It is not closed by `close()`. |

Use the client as a context manager (or call `close()`) to release its connection pool:

```python
with Runearc() as runearc:
    runearc.emails.list()
```

Field names are the API's JSON, in snake case (`reply_to`, `scheduled_at`, `next_cursor`), so the [API reference](https://runearc.com/docs) and this SDK read the same. Requests are `TypedDict`s and responses are plain dicts typed as `TypedDict`s; the shapes are in `runearc.types`. Because `from` is a Python keyword, requests are dicts (`{"from": ...}`), not keyword arguments.

Every method also takes keyword-only `idempotency_key` and `timeout` (seconds, overriding the client's per attempt timeout).

To try things without sending real mail, send to `delivered@test.runearc.com`. `bounced@` and `complained@` at the same domain simulate those outcomes.

## Async

`AsyncRunearc` has the same resources and methods; every call is a coroutine.

```python
import asyncio
from runearc import AsyncRunearc


async def main() -> None:
    async with AsyncRunearc() as runearc:
        sent = await runearc.emails.send(
            {
                "from": "Acme <noreply@acme.com>",
                "to": "bob@example.com",
                "subject": "Hello",
                "text": "Hi Bob",
            }
        )
        print(sent["id"])


asyncio.run(main())
```

Use `async with` (or `await runearc.aclose()`). `http_client` takes an `httpx.AsyncClient`. `webhooks.verify` is a plain function in both clients.

## Errors

Methods return the parsed response and raise `RunearcError` when something fails. There is no `{data, error}` wrapper.

```python
from runearc import Runearc, RunearcError

try:
    runearc.emails.send(
        {"from": "noreply@acme.com", "to": "bob@example.com", "subject": "Hi", "text": "Hi"}
    )
except RunearcError as e:
    print(e.status)  # 403
    print(e.code)  # "sender_not_verified", a stable string to branch on
    print(e.kind)  # "forbidden"
    print(e.message)
    print(e.fix)  # what to do about it, when there is something to do
```

`RunearcError` has `status` (the HTTP status, or `0` when no response came back), `code`, `kind`, `message`, `fix`, `retry_after` (seconds, when the API sent `Retry-After`) and, for a refused batch, `errors`. `kind` is derived from the status: `invalid` (400), `unauthorized` (401), `limit_reached` (402), `forbidden` (403), `not_found` (404), `conflict` (409), `rate_limited` (429), `unavailable` (503), `internal` (other 5xx) and `client` when no response arrived. Failures before a response use the codes `network_error`, `timeout`, `invalid_response` and `missing_api_key`. `e.to_dict()` gives JSON friendly data.

Lists come back as the API sends them: `{"data": [...]}`, plus `next_cursor` on paginated lists.

## Emails

```python
# Send
sent = runearc.emails.send(
    {
        "from": "Acme <noreply@acme.com>",
        "to": ["bob@example.com"],
        "cc": "carol@example.com",
        "reply_to": "support@acme.com",
        "subject": "Your receipt",
        "html": "<p>Thanks for your order.</p>",
        "text": "Thanks for your order.",
        "attachments": [{"filename": "receipt.pdf", "content": base64_pdf}],
        "tags": [{"name": "kind", "value": "receipt"}],
    }
)

# Read
email = runearc.emails.get(sent["id"])  # status, last_event, recipients, html, text
page = runearc.emails.list(limit=50)
more = runearc.emails.list(cursor=page["next_cursor"])
```

### Scheduled send

Pass `scheduled_at` as a `datetime` (a naive one is local time), an RFC 3339 time with an offset, or the relative form `in <n> <unit>`. Up to 30 days ahead.

```python
from datetime import datetime, timedelta, timezone

sent = runearc.emails.send(
    {
        "from": "Acme <noreply@acme.com>",
        "to": "bob@example.com",
        "subject": "Your trial ends tomorrow",
        "text": "Here is what happens next.",
        "scheduled_at": datetime.now(timezone.utc)
        + timedelta(days=1),  # or "2026-10-20T09:00:00Z", or "in 1 day"
    }
)

runearc.emails.get(sent["id"])  # status: "scheduled", scheduled_at: "..."
runearc.emails.reschedule(sent["id"], "in 2 days")  # same as emails.update(id, scheduled_at=...)
runearc.emails.cancel(sent["id"])  # status: "canceled"
```

Rescheduling or canceling an email that is no longer scheduled raises `email_not_scheduled` (409).

### Batch send

Up to 100 emails in one request. Each item is the same shape as `emails.send`.

```python
res = runearc.emails.batch(
    [
        {
            "from": "Acme <noreply@acme.com>",
            "to": "bob@example.com",
            "subject": "Hi Bob",
            "text": "...",
        },
        {
            "from": "Acme <noreply@acme.com>",
            "to": "carol@example.com",
            "subject": "Hi Carol",
            "text": "...",
        },
    ]
)
# res["data"]: [{"id": ...}, {"id": ...}] in request order
```

By default validation is strict: if any item is invalid nothing is sent, and the error lists every bad item.

```python
try:
    runearc.emails.batch(emails)
except RunearcError as e:
    if e.code == "invalid_batch":
        for item in e.errors or []:
            print(item["index"], item["code"], item["message"], item.get("fix"))
```

With `batch_validation="permissive"` the valid items are sent and the invalid ones are reported:

```python
res = runearc.emails.batch(emails, batch_validation="permissive")
res["data"], res.get("errors", [])
```

### Senders

```python
runearc.senders.list()["data"]  # addresses you can use as "from"
```

## Inboxes and messages

Inboxes receive mail. Give one to an agent, wait for a message, read the code in it, reply.

```python
inbox = runearc.inboxes.create({"local_part": "signup-bot", "display_name": "Signup bot"})
runearc.inboxes.list()
runearc.inboxes.get(inbox["id"])

# Wait up to 120 seconds for a matching message. Returns None when none arrived.
got = runearc.messages.wait(inbox["id"], from_="github.com", subject="verify", timeout=60)
if got:
    print(got["codes"], got["links"], got["message"]["subject"])

page = runearc.messages.list(inbox["id"], limit=20)
message = runearc.messages.get(page["data"][0]["id"])
rfc822 = runearc.messages.raw(message["id"])

# Send from the inbox, and reply in a thread
runearc.messages.send(inbox["id"], {"to": "bob@example.com", "subject": "Hello", "text": "Hi Bob"})
runearc.messages.reply(message["id"], {"text": "Thanks, on it", "reply_all": False})

# Threads
runearc.threads.list(inbox["id"])
thread = runearc.threads.get(message["thread_id"])  # with "messages"

runearc.inboxes.delete(inbox["id"])
```

In `messages.wait`, `timeout` is how many seconds to wait (1 to 120, default 30), `from_` is sent as `from`, `to` and `after` also filter, and `request_timeout` overrides the HTTP timeout (default `timeout` plus 15 seconds).

## Conversations

The shared support inbox view across every inbox you can read.

```python
data = runearc.conversations.list(status="open", assignee="none", q="refund")["data"]
runearc.conversations.counts()
runearc.conversations.update(data[0]["id"], {"status": "closed"})
runearc.conversations.update(
    data[0]["id"], {"snoozed_until": datetime.now(timezone.utc) + timedelta(hours=1)}
)
runearc.conversations.read(data[0]["id"])
runearc.conversations.unread(data[0]["id"])
```

## Domains

```python
domain = runearc.domains.create("acme.com")
print(domain["records"])  # the DNS records to publish
print(domain["next_step"])

runearc.domains.verify(domain["id"])  # checks DNS now
runearc.domains.list()
runearc.domains.get("acme.com")  # id or name
runearc.domains.delete(domain["id"])
```

## Webhooks

```python
hook = runearc.webhooks.create(
    {
        "url": "https://acme.com/api/runearc",
        "event_types": ["message.received", "message.delivered", "message.bounced"],
    }
)
# hook["secret"] ("whsec_...") is shown once: store it, you need it to verify deliveries.

runearc.webhooks.list()
deliveries = runearc.webhooks.deliveries(hook["id"], limit=20)["data"]
runearc.webhooks.replay(hook["id"], deliveries[0]["id"])
runearc.webhooks.delete(hook["id"])
```

### Verifying webhooks

Runearc signs every delivery with [Standard Webhooks](https://www.standardwebhooks.com): headers `webhook-id`, `webhook-timestamp` and `webhook-signature` (`v1,<base64 HMAC SHA256>`). `verify_webhook` checks the signature with `hmac` from the standard library, refuses timestamps more than 5 minutes off (`tolerance_seconds` changes that), and returns the parsed event. It raises `WebhookVerificationError` (a `RunearcError`) otherwise. Always pass the raw body, not JSON you parsed and serialized again. `headers` is any mapping with any header case, such as Flask's or Starlette's `request.headers`.

Flask:

```python
import os
from flask import Flask, request
from runearc import WebhookVerificationError, verify_webhook

app = Flask(__name__)


@app.post("/api/runearc")
def runearc_webhook():
    try:
        event = verify_webhook(
            request.get_data(), request.headers, os.environ["RUNEARC_WEBHOOK_SECRET"]
        )
    except WebhookVerificationError as e:
        return str(e), 400
    if event["type"] == "message.received":
        ...  # event["data"]["message_id"], event["data"]["inbox_id"], ...
    return "", 204
```

FastAPI:

```python
import os
from fastapi import FastAPI, HTTPException, Request
from runearc import WebhookVerificationError, verify_webhook

app = FastAPI()


@app.post("/api/runearc", status_code=204)
async def runearc_webhook(request: Request) -> None:
    payload = await request.body()
    try:
        event = verify_webhook(payload, request.headers, os.environ["RUNEARC_WEBHOOK_SECRET"])
    except WebhookVerificationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    print(event["type"], event["id"])  # event["id"] is the webhook-id: use it to drop duplicates
```

`runearc.webhooks.verify(payload, headers, secret)` is the same function.

## API keys, suppressions and events

```python
created = runearc.keys.create({"name": "app", "permissions": "sending", "domain_id": "acme.com"})
# created["secret"] is shown once
runearc.keys.list()
runearc.keys.delete(created["key"]["id"])  # revokes at once

runearc.suppressions.list()
runearc.suppressions.delete("bob@example.com")  # lets mail go to bob again

page = runearc.events.list(limit=100)  # oldest first
page = runearc.events.list(after=page["next_after"])
```

## Retries and idempotency

The SDK retries a request up to `max_retries` times (default 2) with exponential backoff and jitter, and waits as long as the API's `Retry-After` header asks (seconds or an HTTP date, at most 60 seconds). It retries:

* 429 (rate limited), always;
* 5xx, network errors and timeouts, when the request is safe to repeat: GET, PATCH and DELETE, and any POST that carries an `Idempotency-Key`, plus the POSTs that are safe to repeat by nature (`emails.cancel`, `domains.verify`, `conversations.read`).

It never retries other 4xx errors. A POST that could create something twice (such as creating an inbox) is not repeated after a 5xx or a network error; the error is thrown instead.

Every call that sends mail (`emails.send`, `emails.batch`, `messages.send`, `messages.reply`) gets an `Idempotency-Key` automatically when you do not give one, and every retry reuses it, so a retry never sends twice. Runearc remembers keys for 24 hours. To make your own retries safe too, for example from a job queue, pass a key that is stable for that piece of work:

```python
runearc.emails.send(email, idempotency_key=f"welcome/{user.id}")
```

Repeating a key with the same body returns the first result. The same key with a different body raises `idempotency_conflict` (409).

## License

MIT. Copyright Runearc. See `LICENSE`.
