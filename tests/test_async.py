"""The async client against the same real API, plus its retry and timeout paths."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Awaitable, Callable, List, TypeVar

import pytest

from helpers import Stack, unique
from runearc import AsyncRunearc, RunearcError
from runearc.types import SendEmailRequest
from servers import Request, Server, error_body

T = TypeVar("T")


def run(coro: Callable[[], Awaitable[T]]) -> T:
    async def main() -> T:
        return await coro()

    return asyncio.run(main())


def test_send_get_list_and_errors(stack: Stack) -> None:
    async def scenario() -> None:
        async with AsyncRunearc(stack.api_key, base_url=stack.api_url) as mb:
            subject = unique("async send")
            sent = await mb.emails.send(
                {
                    "from": stack.sender(),
                    "to": stack.test_address(),
                    "subject": subject,
                    "text": "hi",
                }
            )
            assert sent["id"].startswith("eml_")
            assert (await mb.emails.get(sent["id"]))["subject"] == subject
            page = await mb.emails.list(limit=100)
            assert any(e["id"] == sent["id"] for e in page["data"])
            senders = await mb.senders.list()
            assert any(s["domain"] == stack.domain for s in senders["data"])

            with pytest.raises(RunearcError) as info:
                await mb.emails.send(
                    {
                        "from": "nobody@not-my-domain.example",
                        "to": stack.test_address(),
                        "subject": "x",
                        "text": "x",
                    }
                )
            assert info.value.status == 403
            assert info.value.code == "sender_not_verified"
            assert info.value.fix

            bad = AsyncRunearc("mb_live_not_a_real_key", base_url=stack.api_url)
            with pytest.raises(RunearcError) as unauth:
                await bad.emails.list()
            await bad.aclose()
            assert unauth.value.code == "invalid_api_key"

    run(scenario)


def test_idempotency_batch_and_scheduling(stack: Stack) -> None:
    async def scenario() -> None:
        async with AsyncRunearc(stack.api_key, base_url=stack.api_url) as mb:
            key = str(uuid.uuid4())
            body: SendEmailRequest = {
                "from": stack.sender(),
                "to": stack.test_address(),
                "subject": unique("async idem"),
                "text": "once",
            }
            a = await mb.emails.send(body, idempotency_key=key)
            b = await mb.emails.send(body, idempotency_key=key)
            assert a["id"] == b["id"]
            with pytest.raises(RunearcError) as conflict:
                await mb.emails.send({**body, "text": "different"}, idempotency_key=key)
            assert conflict.value.code == "idempotency_conflict"

            good: SendEmailRequest = {**body, "subject": unique("async batch")}
            bad: SendEmailRequest = {**good, "from": "nobody@not-my-domain.example"}
            with pytest.raises(RunearcError) as strict:
                await mb.emails.batch([good, bad])
            assert strict.value.code == "invalid_batch"
            assert strict.value.errors is not None
            assert strict.value.errors[0]["index"] == 1
            permissive = await mb.emails.batch([good, bad], batch_validation="permissive")
            assert len(permissive["data"]) == 1
            assert len(permissive.get("errors", [])) == 1

            scheduled = await mb.emails.send({**body, "scheduled_at": "in 2 hours"})
            assert (await mb.emails.get(scheduled["id"]))["status"] == "scheduled"
            await mb.emails.reschedule(scheduled["id"], "in 3 hours")
            await mb.emails.cancel(scheduled["id"])
            assert (await mb.emails.get(scheduled["id"]))["status"] == "canceled"

    run(scenario)


def test_inboxes_messages_conversations_and_wait(stack: Stack) -> None:
    async def scenario() -> None:
        async with AsyncRunearc(stack.api_key, base_url=stack.api_url) as mb:
            inbox = await mb.inboxes.create({"local_part": f"sdk-a{int(time.time() * 1000):x}"})
            sent = await mb.messages.send(
                inbox["id"],
                {"to": stack.test_address(), "subject": unique("async inbox"), "text": "x"},
            )
            assert "Subject:" in await mb.messages.raw(sent["id"])
            reply = await mb.messages.reply(sent["id"], {"text": "follow up"})
            assert reply["thread_id"] == sent["thread_id"]
            thread = await mb.threads.get(sent["thread_id"])
            assert {sent["id"], reply["id"]} <= {m["id"] for m in thread["messages"]}
            assert any(
                t["id"] == sent["thread_id"] for t in (await mb.threads.list(inbox["id"]))["data"]
            )
            assert any(m["id"] == sent["id"] for m in (await mb.messages.list(inbox["id"]))["data"])
            assert (await mb.messages.get(sent["id"]))["id"] == sent["id"]
            assert await mb.messages.wait(inbox["id"], timeout=1) is None

            updated = await mb.conversations.update(sent["thread_id"], {"status": "closed"})
            assert updated["status"] == "closed"
            await mb.conversations.read(sent["thread_id"])
            await mb.conversations.unread(sent["thread_id"])
            assert isinstance((await mb.conversations.counts())["open"], int)
            listed = await mb.conversations.list(inbox_id=inbox["id"], status="all")
            assert isinstance(listed["data"], list)

            await mb.inboxes.delete(inbox["id"])
            with pytest.raises(RunearcError) as gone:
                await mb.inboxes.get(inbox["id"])
            assert gone.value.status == 404

    run(scenario)


def test_domains_keys_webhooks_suppressions_and_events(stack: Stack) -> None:
    async def scenario() -> None:
        async with AsyncRunearc(stack.api_key, base_url=stack.api_url) as mb:
            name = f"sdk-async-{int(time.time() * 1000):x}.example.com"
            added = await mb.domains.create(name)
            assert (await mb.domains.get(name))["id"] == added["id"]
            assert (await mb.domains.verify(added["id"]))["status"] != "verified"
            assert any(d["id"] == added["id"] for d in (await mb.domains.list())["data"])
            await mb.domains.delete(added["id"])

            created = await mb.keys.create({"name": "async key", "permissions": "sending"})
            assert any(k["id"] == created["key"]["id"] for k in (await mb.keys.list())["data"])
            await mb.keys.delete(created["key"]["id"])

            hook = await mb.webhooks.create({"url": "http://127.0.0.1:9/hook"})
            assert any(w["id"] == hook["id"] for w in (await mb.webhooks.list())["data"])
            assert (await mb.webhooks.deliveries(hook["id"], limit=5))["data"] == []
            assert hook["secret"].startswith("whsec_")
            await mb.webhooks.delete(hook["id"])

            assert isinstance((await mb.suppressions.list())["data"], list)
            events = await mb.events.list(limit=10)
            assert isinstance(events["data"], list)

    run(scenario)


def test_retries_honour_retry_after_and_stop_after_max_retries() -> None:
    attempts: List[float] = []

    def respond(req: Request) -> None:
        attempts.append(time.monotonic())
        if len(attempts) == 1:
            req.reply(503, error_body("unavailable", "try again"), {"retry-after": "1"})
        else:
            req.reply(200, {"data": []})

    server = Server(respond)
    try:

        async def scenario() -> None:
            async with AsyncRunearc("mb_test", base_url=server.url) as mb:
                assert await mb.inboxes.list() == {"data": []}

        run(scenario)
        assert len(attempts) == 2
        assert attempts[1] - attempts[0] >= 0.95
    finally:
        server.close()

    refusing = Server(
        lambda req: req.reply(
            429, error_body("rate_limited", "slow down", "wait"), {"retry-after": "0"}
        )
    )
    try:

        async def exhausted() -> None:
            async with AsyncRunearc("mb_test", base_url=refusing.url, max_retries=2) as mb:
                with pytest.raises(RunearcError) as info:
                    await mb.inboxes.create()
                assert info.value.code == "rate_limited"
                assert info.value.retry_after == 0

        run(exhausted)
        assert len(refusing.requests) == 3
    finally:
        refusing.close()


def test_network_errors_timeouts_and_unrepeatable_posts() -> None:
    dropping = Server(lambda req: req.drop())
    slow = Server(lambda req: time.sleep(1.5))
    failing = Server(lambda req: req.reply(503, error_body("unavailable", "no")))
    try:

        async def scenario() -> None:
            mb = AsyncRunearc("mb_test", base_url=dropping.url, max_retries=1)
            with pytest.raises(RunearcError) as net:
                await mb.domains.list()
            await mb.aclose()
            assert net.value.code == "network_error"
            assert len(dropping.requests) == 2

            mb = AsyncRunearc("mb_test", base_url=slow.url, max_retries=0, timeout=0.2)
            with pytest.raises(RunearcError) as late:
                await mb.events.list()
            await mb.aclose()
            assert late.value.code == "timeout"

            mb = AsyncRunearc("mb_test", base_url=failing.url)
            with pytest.raises(RunearcError) as once:
                await mb.inboxes.create({"local_part": "x"})
            await mb.aclose()
            assert once.value.status == 503
            assert len(failing.requests) == 1

        run(scenario)
    finally:
        dropping.close()
        slow.close()
        failing.close()


def test_a_send_through_a_lost_response_keeps_its_key(stack: Stack) -> None:
    seen: List[str] = []

    def respond(req: Request) -> None:
        seen.append(req.headers["idempotency-key"])
        if len(seen) == 1:
            req.reply(503, error_body("unavailable", "lost"), {"retry-after": "0"})
        else:
            req.reply(200, {"id": "eml_1"})

    server = Server(respond)
    try:

        async def scenario() -> None:
            async with AsyncRunearc("mb_test", base_url=server.url) as mb:
                sent = await mb.emails.send(
                    {"from": stack.sender(), "to": "a@b.c", "subject": "s", "text": "t"}
                )
                assert sent["id"] == "eml_1"

        run(scenario)
        assert len(seen) == 2
        assert seen[0] == seen[1]
        assert json.loads(server.requests[0].body)["subject"] == "s"
    finally:
        server.close()
