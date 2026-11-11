from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import List

import pytest

from helpers import Stack, eventually, unique
from runearc import Runearc, RunearcError
from runearc.types import SendEmailRequest


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_send_to_a_simulated_recipient_and_read_it_back(mb: Runearc, stack: Stack) -> None:
    subject = unique("send")
    sent = mb.emails.send(
        {
            "from": stack.sender(),
            "to": stack.test_address(),
            "subject": subject,
            "html": "<p>Hello</p>",
            "text": "Hello",
            "tags": [{"name": "kind", "value": "test"}],
        }
    )
    email_id = sent["id"]
    assert email_id.startswith("eml_")

    def delivered() -> object:
        e = mb.emails.get(email_id)
        return e if e["status"] == "delivered" else None

    got = eventually(delivered)
    assert isinstance(got, dict)
    assert got["object"] == "email"
    assert got["subject"] == subject
    assert got["to"] == [stack.test_address()]
    assert got["recipients"][0]["status"] == "delivered"
    assert got["tags"] == [{"name": "kind", "value": "test"}]

    page = mb.emails.list(limit=100)
    assert any(e["id"] == email_id for e in page["data"])
    assert "next_cursor" in page


def test_same_idempotency_key_returns_the_same_id_and_a_different_body_is_refused(
    mb: Runearc, stack: Stack
) -> None:
    key = str(uuid.uuid4())
    body: SendEmailRequest = {
        "from": stack.sender(),
        "to": stack.test_address(),
        "subject": unique("idem"),
        "text": "once",
    }
    a = mb.emails.send(body, idempotency_key=key)
    b = mb.emails.send(body, idempotency_key=key)
    assert b["id"] == a["id"]
    with pytest.raises(RunearcError) as info:
        mb.emails.send({**body, "text": "different"}, idempotency_key=key)
    assert info.value.status == 409
    assert info.value.code == "idempotency_conflict"
    assert info.value.kind == "conflict"


def test_typed_errors_carry_status_code_kind_and_fix(mb: Runearc, stack: Stack) -> None:
    with pytest.raises(RunearcError) as info:
        mb.emails.send(
            {
                "from": "nobody@not-my-domain.example",
                "to": stack.test_address(),
                "subject": "x",
                "text": "x",
            }
        )
    err = info.value
    assert err.status == 403
    assert err.code == "sender_not_verified"
    assert err.kind == "forbidden"
    assert err.fix
    assert err.message in str(err)
    assert err.to_dict()["code"] == "sender_not_verified"

    bad = Runearc("mb_live_not_a_real_key", base_url=stack.api_url)
    with pytest.raises(RunearcError) as unauth:
        bad.emails.list()
    assert unauth.value.status == 401
    assert unauth.value.code == "invalid_api_key"
    assert unauth.value.fix

    with pytest.raises(RunearcError) as missing:
        mb.emails.get("eml_00000000000000000000000000")
    assert missing.value.status == 404
    assert missing.value.kind == "not_found"


def test_senders_include_the_verified_domain(mb: Runearc, stack: Stack) -> None:
    data = mb.senders.list()["data"]
    assert any(s["domain"] == stack.domain and s["verified"] for s in data)


def test_batch_sends_every_valid_item_and_returns_ids_in_order(mb: Runearc, stack: Stack) -> None:
    subjects = [unique("batch a"), unique("batch b"), unique("batch c")]
    res = mb.emails.batch(
        [
            {"from": stack.sender(), "to": stack.test_address(), "subject": s, "text": "hi"}
            for s in subjects
        ]
    )
    assert len(res["data"]) == 3
    for item, subject in zip(res["data"], subjects):
        assert mb.emails.get(item["id"])["subject"] == subject
    assert res.get("errors", []) == []


def test_batch_strict_refuses_the_whole_batch_with_item_errors(mb: Runearc, stack: Stack) -> None:
    subject = unique("strict")
    good: SendEmailRequest = {
        "from": stack.sender(),
        "to": stack.test_address(),
        "subject": subject,
        "text": "ok",
    }
    bad: SendEmailRequest = {**good, "from": "nobody@not-my-domain.example", "text": "bad"}
    with pytest.raises(RunearcError) as info:
        mb.emails.batch([good, bad, good])
    err = info.value
    assert err.status == 400
    assert err.code == "invalid_batch"
    assert "emails[1]" in err.message
    assert err.errors is not None
    assert len(err.errors) == 1
    assert err.errors[0]["index"] == 1
    assert err.errors[0]["code"]
    # Nothing was stored.
    page = mb.emails.list(limit=100)
    assert [e for e in page["data"] if e["subject"] == subject] == []


def test_batch_permissive_sends_valid_items_and_reports_invalid_ones(
    mb: Runearc, stack: Stack
) -> None:
    res = mb.emails.batch(
        [
            {
                "from": stack.sender(),
                "to": stack.test_address(),
                "subject": unique("perm"),
                "text": "ok",
            },
            {
                "from": "nobody@not-my-domain.example",
                "to": stack.test_address(),
                "subject": "bad",
                "text": "bad",
            },
        ],
        batch_validation="permissive",
    )
    assert len(res["data"]) == 1
    errors = res.get("errors", [])
    assert len(errors) == 1
    assert errors[0]["index"] == 1


def test_batch_repeats_with_the_same_key_return_the_same_ids(mb: Runearc, stack: Stack) -> None:
    items: List[SendEmailRequest] = [
        {
            "from": stack.sender(),
            "to": stack.test_address(),
            "subject": unique("batch idem"),
            "text": "x",
        }
    ]
    key = str(uuid.uuid4())
    a = mb.emails.batch(items, idempotency_key=key)
    b = mb.emails.batch(items, idempotency_key=key)
    assert b["data"] == a["data"]


def test_schedule_reschedule_and_cancel(mb: Runearc, stack: Stack) -> None:
    when = datetime.now(timezone.utc) + timedelta(hours=1)
    sent = mb.emails.send(
        {
            "from": stack.sender(),
            "to": stack.test_address(),
            "subject": unique("scheduled"),
            "text": "later",
            "scheduled_at": when,
        }
    )
    email_id = sent["id"]
    e = mb.emails.get(email_id)
    assert e["status"] == "scheduled"
    assert e["last_event"] == "scheduled"
    assert abs((parse_time(e["scheduled_at"] or "") - when).total_seconds()) < 1

    later = datetime.now(timezone.utc) + timedelta(hours=2)
    assert mb.emails.reschedule(email_id, later) == {"object": "email", "id": email_id}
    after = mb.emails.get(email_id)
    assert abs((parse_time(after["scheduled_at"] or "") - later).total_seconds()) < 1

    assert mb.emails.cancel(email_id) == {"object": "email", "id": email_id}
    assert mb.emails.get(email_id)["status"] == "canceled"

    with pytest.raises(RunearcError) as info:
        mb.emails.update(email_id, scheduled_at="in 1 hour")
    assert info.value.status == 409
    assert info.value.code == "email_not_scheduled"


def test_naive_datetimes_are_local_time(mb: Runearc, stack: Stack) -> None:
    naive = datetime.now() + timedelta(hours=3)
    email_id = mb.emails.send(
        {
            "from": stack.sender(),
            "to": stack.test_address(),
            "subject": unique("naive"),
            "text": "later",
            "scheduled_at": naive,
        }
    )["id"]
    got = parse_time(mb.emails.get(email_id)["scheduled_at"] or "")
    assert abs((got - naive.astimezone(timezone.utc)).total_seconds()) < 1
    mb.emails.cancel(email_id)


def test_relative_form_works_and_times_without_an_offset_are_refused(
    mb: Runearc, stack: Stack
) -> None:
    email_id = mb.emails.send(
        {
            "from": stack.sender(),
            "to": stack.test_address(),
            "subject": unique("relative"),
            "text": "later",
            "scheduled_at": "in 2 hours",
        }
    )["id"]
    assert mb.emails.get(email_id)["status"] == "scheduled"
    mb.emails.cancel(email_id)

    with pytest.raises(RunearcError) as info:
        mb.emails.send(
            {
                "from": stack.sender(),
                "to": stack.test_address(),
                "subject": "x",
                "text": "x",
                "scheduled_at": "tomorrow at 9am",
            }
        )
    assert info.value.status == 400
    assert info.value.code == "invalid_scheduled_at"
    assert info.value.fix


def test_a_sent_email_cannot_be_canceled(mb: Runearc, stack: Stack) -> None:
    email_id = mb.emails.send(
        {
            "from": stack.sender(),
            "to": stack.test_address(),
            "subject": unique("now"),
            "text": "now",
        }
    )["id"]
    with pytest.raises(RunearcError) as info:
        mb.emails.cancel(email_id)
    assert info.value.status == 409
    assert info.value.code == "email_not_scheduled"
