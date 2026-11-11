"""Retry behaviour. A tiny local HTTP server stands in for failures the real API cannot be made
to produce on demand. The idempotency test puts that server in front of the REAL API as a proxy:
the first send reaches the API and sends, then the proxy answers 503 as if the response was lost;
the SDK's retry must reuse the same Idempotency-Key so the API returns the first email instead of
sending a second one."""

from __future__ import annotations

import re
import time
import urllib.request
from typing import Callable, Iterator

import pytest

from helpers import Stack, unique
from runearc import DEFAULT_BASE_URL, Runearc, RunearcError
from servers import Request, Responder, Server, error_body

UNAVAILABLE = error_body("unavailable", "try again")


@pytest.fixture
def serve() -> Iterator[Callable[[Responder], Server]]:
    servers: list[Server] = []

    def start(responder: Responder) -> Server:
        server = Server(responder)
        servers.append(server)
        return server

    yield start
    for s in servers:
        s.close()


def test_get_retries_on_503_and_honours_retry_after(serve: Callable[[Responder], Server]) -> None:
    times: list[float] = []

    def respond(req: Request) -> None:
        times.append(time.monotonic())
        if len(times) == 1:
            req.reply(503, UNAVAILABLE, {"retry-after": "1"})
        else:
            req.reply(200, {"data": []})

    server = serve(respond)
    mb = Runearc("mb_test", base_url=server.url)
    assert mb.inboxes.list() == {"data": []}
    assert len(times) == 2
    assert times[1] - times[0] >= 0.95


def test_retry_after_as_an_http_date_is_honoured(serve: Callable[[Responder], Server]) -> None:
    from email.utils import formatdate

    times: list[float] = []

    def respond(req: Request) -> None:
        times.append(time.monotonic())
        if len(times) == 1:
            req.reply(503, UNAVAILABLE, {"retry-after": formatdate(time.time() + 2, usegmt=True)})
        else:
            req.reply(200, {"data": []})

    mb = Runearc("mb_test", base_url=serve(respond).url)
    mb.keys.list()
    assert times[1] - times[0] >= 0.9


def test_429_is_retried_even_on_a_post_without_a_key_and_gives_up_after_max_retries(
    serve: Callable[[Responder], Server],
) -> None:
    def respond(req: Request) -> None:
        req.reply(429, error_body("rate_limited", "slow down", "wait"), {"retry-after": "0"})

    server = serve(respond)
    mb = Runearc("mb_test", base_url=server.url, max_retries=3)
    with pytest.raises(RunearcError) as info:
        mb.inboxes.create()
    assert len(server.requests) == 4
    err = info.value
    assert err.status == 429
    assert err.code == "rate_limited"
    assert err.kind == "rate_limited"
    assert err.retry_after == 0


def test_other_4xx_are_never_retried(serve: Callable[[Responder], Server]) -> None:
    server = serve(lambda req: req.reply(400, error_body("invalid_body", "bad", "fix it")))
    mb = Runearc("mb_test", base_url=server.url)
    with pytest.raises(RunearcError) as info:
        mb.emails.send({"from": "a@b.c", "to": "d@e.f", "subject": "s"})
    assert len(server.requests) == 1
    assert info.value.code == "invalid_body"
    assert info.value.fix == "fix it"


def test_a_post_that_could_create_twice_is_not_repeated_on_5xx(
    serve: Callable[[Responder], Server],
) -> None:
    server = serve(lambda req: req.reply(503, UNAVAILABLE))
    mb = Runearc("mb_test", base_url=server.url)
    with pytest.raises(RunearcError) as info:
        mb.inboxes.create({"local_part": "x"})
    assert len(server.requests) == 1
    assert info.value.status == 503
    assert info.value.kind == "unavailable"


def test_a_post_with_an_idempotency_key_is_repeated_on_5xx(
    serve: Callable[[Responder], Server],
) -> None:
    def respond(req: Request) -> None:
        if len(server.requests) == 1:
            req.reply(503, UNAVAILABLE, {"retry-after": "0"})
        else:
            req.reply(200, {"object": "email", "id": "eml_1"})

    server = serve(respond)
    mb = Runearc("mb_test", base_url=server.url)
    assert mb.emails.cancel("eml_1")["id"] == "eml_1"
    assert len(server.requests) == 2


def test_network_errors_are_retried_and_reported_typed_when_they_persist(
    serve: Callable[[Responder], Server],
) -> None:
    server = serve(lambda req: req.drop())
    mb = Runearc("mb_test", base_url=server.url, max_retries=1)
    with pytest.raises(RunearcError) as info:
        mb.domains.list()
    assert len(server.requests) == 2
    assert info.value.status == 0
    assert info.value.code == "network_error"
    assert info.value.kind == "client"


def test_each_attempt_times_out(serve: Callable[[Responder], Server]) -> None:
    server = serve(lambda req: time.sleep(1.5))
    mb = Runearc("mb_test", base_url=server.url, max_retries=0, timeout=0.2)
    with pytest.raises(RunearcError) as info:
        mb.events.list()
    assert info.value.code == "timeout"
    assert info.value.status == 0


def test_a_per_call_timeout_overrides_the_client_one(serve: Callable[[Responder], Server]) -> None:
    server = serve(lambda req: time.sleep(1.5))
    mb = Runearc("mb_test", base_url=server.url, max_retries=0)
    with pytest.raises(RunearcError) as info:
        mb.events.list(timeout=0.2)
    assert info.value.code == "timeout"


def test_a_response_that_is_not_json_is_an_invalid_response(
    serve: Callable[[Responder], Server],
) -> None:
    server = serve(lambda req: req.reply(200, raw=b"<html>nope</html>"))
    mb = Runearc("mb_test", base_url=server.url)
    with pytest.raises(RunearcError) as info:
        mb.keys.list()
    assert info.value.code == "invalid_response"


def test_an_error_without_the_api_shape_falls_back_to_http_status(
    serve: Callable[[Responder], Server],
) -> None:
    server = serve(lambda req: req.reply(404, raw=b"not here"))
    mb = Runearc("mb_test", base_url=server.url)
    with pytest.raises(RunearcError) as info:
        mb.keys.list()
    assert info.value.code == "http_404"
    assert info.value.message == "not here"
    assert info.value.fix is None


def test_requests_carry_auth_user_agent_and_extra_headers(
    serve: Callable[[Responder], Server],
) -> None:
    server = serve(lambda req: req.reply(200, {"data": []}))
    mb = Runearc("mb_secret", base_url=server.url + "/v1/", headers={"Runearc-Project": "prj_1"})
    mb.inboxes.list()
    req = server.requests[0]
    assert req.path == "/v1/inboxes"
    assert req.headers["authorization"] == "Bearer mb_secret"
    assert req.headers["runearc-project"] == "prj_1"
    assert re.fullmatch(r"runearc-python/\d+\.\d+\.\d+", req.headers["user-agent"])
    assert "idempotency-key" not in req.headers


def test_a_retried_send_reuses_its_idempotency_key_so_the_real_api_sends_once(
    serve: Callable[[Responder], Server], mb: Runearc, stack: Stack
) -> None:
    def respond(req: Request) -> None:
        upstream = urllib.request.Request(
            stack.api_url + req.path,
            data=req.body,
            method=req.method,
            headers={
                "authorization": req.headers["authorization"],
                "content-type": "application/json",
                "idempotency-key": req.headers["idempotency-key"],
            },
        )
        with urllib.request.urlopen(upstream, timeout=30) as res:
            status, payload = res.status, res.read()
        # The first answer is "lost": the email was sent, but the client sees a 503.
        if len(proxy.requests) == 1:
            req.reply(503, UNAVAILABLE)
        else:
            req.reply(status, raw=payload)

    proxy = serve(respond)
    proxied = Runearc(stack.api_key, base_url=proxy.url)
    subject = unique("retry once")
    sent = proxied.emails.send(
        {"from": stack.sender(), "to": stack.test_address(), "subject": subject, "text": "once"}
    )
    assert len(proxy.requests) == 2
    first, second = (r.headers["idempotency-key"] for r in proxy.requests)
    assert re.fullmatch(r"[0-9a-f-]{36}", first)
    assert second == first
    page = mb.emails.list(limit=100)
    assert [e["id"] for e in page["data"] if e["subject"] == subject] == [sent["id"]]


def test_api_key_and_base_url_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUNEARC_API_KEY", raising=False)
    monkeypatch.delenv("RUNEARC_BASE_URL", raising=False)
    with pytest.raises(RunearcError) as info:
        Runearc()
    assert info.value.code == "missing_api_key"
    assert info.value.fix
    monkeypatch.setenv("RUNEARC_API_KEY", "mb_from_env")
    assert Runearc().base_url == "https://api.runearc.com" == DEFAULT_BASE_URL
    monkeypatch.setenv("RUNEARC_BASE_URL", "http://127.0.0.1:1/v1/")
    assert Runearc().base_url == "http://127.0.0.1:1"
    assert Runearc(base_url="http://example.test/v1").base_url == "http://example.test"
