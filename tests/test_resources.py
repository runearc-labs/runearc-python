from __future__ import annotations

import time
import uuid

import pytest

from helpers import Stack, psql, unique
from runearc import Runearc, RunearcError
from runearc.types import SendMessageRequest


def test_inboxes_messages_threads_and_conversations(mb: Runearc, stack: Stack) -> None:
    local = f"sdk-{int(time.time() * 1000):x}"
    inbox = mb.inboxes.create({"local_part": local, "display_name": "SDK"})
    assert inbox["id"].startswith("ibx_")
    assert inbox["local_part"] == local
    assert inbox["domain"].endswith(stack.shared_domain)
    assert mb.inboxes.get(inbox["id"])["address"] == inbox["address"]
    assert any(i["id"] == inbox["id"] for i in mb.inboxes.list()["data"])

    subject = unique("inbox send")
    sent = mb.messages.send(
        inbox["id"], {"to": stack.test_address(), "subject": subject, "text": "Hello from an inbox"}
    )
    assert sent["id"].startswith("msg_")
    assert sent["subject"] == subject

    # Same key twice: one message.
    key = str(uuid.uuid4())
    message: SendMessageRequest = {
        "to": stack.test_address(),
        "subject": unique("inbox idem"),
        "text": "x",
    }
    a = mb.messages.send(inbox["id"], message, idempotency_key=key)
    b = mb.messages.send(inbox["id"], message, idempotency_key=key)
    assert b["id"] == a["id"]

    page = mb.messages.list(inbox["id"], limit=10)
    assert any(m["id"] == sent["id"] for m in page["data"])
    got = mb.messages.get(sent["id"])
    assert got["thread_id"] == sent["thread_id"]
    assert f"Subject: {subject}" in mb.messages.raw(sent["id"])

    reply = mb.messages.reply(sent["id"], {"text": "A follow up"})
    assert reply["thread_id"] == sent["thread_id"]
    assert reply["in_reply_to"] == sent["rfc_message_id"]

    threads = mb.threads.list(inbox["id"])
    assert any(t["id"] == sent["thread_id"] for t in threads["data"])
    thread = mb.threads.get(sent["thread_id"])
    ids = {m["id"] for m in thread["messages"]}
    assert {sent["id"], reply["id"]} <= ids

    # Nothing inbound arrives (no SMTP server in this run): wait returns None.
    assert mb.messages.wait(inbox["id"], timeout=1) is None

    conversations = mb.conversations.list(inbox_id=inbox["id"], status="all")
    assert isinstance(conversations["data"], list)
    assert isinstance(mb.conversations.counts()["open"], int)
    assert mb.conversations.update(sent["thread_id"], {"status": "closed"})["status"] == "closed"
    assert mb.conversations.update(sent["thread_id"], {"status": "open"})["status"] == "open"
    mb.conversations.read(sent["thread_id"])
    mb.conversations.unread(sent["thread_id"])

    mb.inboxes.delete(inbox["id"])
    with pytest.raises(RunearcError) as info:
        mb.inboxes.get(inbox["id"])
    assert info.value.status == 404


def test_domains_crud_and_verify(mb: Runearc) -> None:
    name = f"sdk-crud-{int(time.time() * 1000):x}.example.com"
    added = mb.domains.create(name)
    assert added["name"] == name
    assert added["status"] == "pending"
    assert len(added["records"]) > 0
    assert any(d["id"] == added["id"] for d in mb.domains.list()["data"])
    assert mb.domains.get(added["id"])["name"] == name
    assert mb.domains.get(name)["id"] == added["id"]
    checked = mb.domains.verify(added["id"])
    assert checked["id"] == added["id"]
    assert checked["status"] != "verified"
    mb.domains.delete(added["id"])
    with pytest.raises(RunearcError) as info:
        mb.domains.get(added["id"])
    assert info.value.status == 404


def test_keys_create_use_list_and_revoke(mb: Runearc, stack: Stack) -> None:
    created = mb.keys.create(
        {"name": "sdk sending", "permissions": "sending", "domain_id": stack.domain_id}
    )
    assert created["secret"]
    assert created["key"]["permissions"] == "sending"
    assert any(k["id"] == created["key"]["id"] for k in mb.keys.list()["data"])

    with Runearc(created["secret"], base_url=stack.api_url) as sender:
        sent = sender.emails.send(
            {
                "from": f"app@{stack.domain}",
                "to": stack.test_address(),
                "subject": unique("sending key"),
                "text": "x",
            }
        )
        assert sent["id"].startswith("eml_")
        # A sending key cannot manage inboxes.
        with pytest.raises(RunearcError) as forbidden:
            sender.inboxes.create()
        assert forbidden.value.status == 403

        mb.keys.delete(created["key"]["id"])
        with pytest.raises(RunearcError) as revoked:
            sender.emails.list()
        assert revoked.value.status == 401


def test_suppressions_and_events(mb: Runearc, stack: Stack) -> None:
    # The API has no POST for suppressions (bounces and complaints add them, and simulated
    # test recipients are never suppressed), so one row is added with SQL, the way core's
    # add_suppression stores it.
    email_id = mb.emails.send(
        {
            "from": f"app@{stack.domain}",
            "to": stack.test_address(),
            "subject": unique("events"),
            "text": "x",
        }
    )["id"]
    project_id = mb.emails.get(email_id)["project_id"]
    address = f"blocked-{int(time.time() * 1000):x}@example.org"
    psql(
        stack.db_url,
        "INSERT INTO suppressions (org_id, project_id, address, reason) "
        f"VALUES ('{stack.org_id}', '{project_id}', '{address}', 'manual')",
    )
    found = [s for s in mb.suppressions.list()["data"] if s["address"] == address]
    assert found[0]["reason"] == "manual"
    mb.suppressions.delete(address)
    assert not any(s["address"] == address for s in mb.suppressions.list()["data"])

    first = mb.events.list(limit=100)
    assert len(first["data"]) > 0
    assert any(e["type"].startswith("message.") for e in first["data"])
    assert first["next_after"] == first["data"][-1]["id"]
    nxt = mb.events.list(after=first["next_after"] or "", limit=100)
    assert all(e["id"] > (first["next_after"] or "") for e in nxt["data"])
