"""The synchronous client."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from datetime import datetime
from types import TracebackType
from typing import Any, Optional, Type, Union, cast

import httpx

from ._http import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    Call,
    QueryValue,
    Settings,
    backoff_seconds,
    email_body,
    iso,
    parse_error,
    parse_success,
    seg,
    wait_params,
)
from .errors import RunearcError
from .types import (
    AddedDomain,
    BatchResponse,
    Conversation,
    ConversationCounts,
    ConversationPage,
    ConversationUpdate,
    CreatedKey,
    CreatedWebhook,
    CreateInboxRequest,
    CreateKeyRequest,
    CreateWebhookRequest,
    Delivery,
    DeliveryList,
    Domain,
    DomainList,
    EmailDetail,
    EmailPage,
    EmailRef,
    EventPage,
    Inbox,
    InboxList,
    KeyList,
    Message,
    MessagePage,
    ReplyRequest,
    SendEmailRequest,
    SendEmailResponse,
    SenderList,
    SendMessageRequest,
    SuppressionList,
    ThreadList,
    ThreadWithMessages,
    TimeInput,
    Waited,
    WebhookEvent,
    WebhookList,
)
from .webhooks import WEBHOOK_TOLERANCE_SECONDS, verify_webhook


class Runearc:
    """The Runearc API client.

    ``api_key`` defaults to the ``RUNEARC_API_KEY`` environment variable and
    ``base_url`` to ``RUNEARC_BASE_URL``, else ``https://api.runearc.com`` (a
    trailing ``/v1`` is fine). ``max_retries`` counts retries after the first
    attempt, on 429, 5xx and network errors. ``timeout`` is per attempt, in
    seconds. ``headers`` are sent with every request, such as ``Runearc-Project``.
    ``http_client`` is your own ``httpx.Client``; it is not closed by ``close()``.

    Every method also takes keyword-only ``idempotency_key`` and ``timeout``.
    ``idempotency_key`` makes a request safe to repeat: the API returns the first
    result for the same key for 24 hours. Mail sending calls get a random one when
    you give none. ``timeout`` overrides the client's per attempt timeout.
    """

    base_url: str
    max_retries: int
    timeout: float

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: float = DEFAULT_TIMEOUT,
        headers: Optional[Mapping[str, str]] = None,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self._settings = Settings(api_key, base_url, max_retries, timeout, headers)
        self.base_url = self._settings.base_url
        self.max_retries = self._settings.max_retries
        self.timeout = timeout
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(follow_redirects=False)
        self.emails = Emails(self)
        self.senders = Senders(self)
        self.inboxes = Inboxes(self)
        self.messages = Messages(self)
        self.threads = Threads(self)
        self.conversations = Conversations(self)
        self.domains = Domains(self)
        self.webhooks = Webhooks(self)
        self.keys = Keys(self)
        self.suppressions = Suppressions(self)
        self.events = Events(self)

    def close(self) -> None:
        """Closes the connection pool, unless you passed your own ``http_client``."""
        if self._owns_client:
            self._http.close()

    def __enter__(self) -> Runearc:
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.close()

    def _call(self, call: Call) -> Any:
        """Every API call goes through here."""
        s = self._settings
        prepared = s.prepare(call)
        attempt = 0
        while True:
            try:
                response = self._http.request(
                    call.method,
                    prepared.url,
                    headers=prepared.headers,
                    content=prepared.content,
                    timeout=prepared.timeout,
                )
            except httpx.HTTPError as exc:
                if prepared.repeatable and attempt < s.max_retries:
                    time.sleep(backoff_seconds(attempt))
                    attempt += 1
                    continue
                raise s.transport_error(exc, prepared) from exc
            if response.is_success:
                return parse_success(response, call.text)
            delay = s.retry_delay(response, prepared, attempt)
            if delay is None:
                raise parse_error(response)
            time.sleep(delay)
            attempt += 1


class _Resource:
    def __init__(self, client: Runearc) -> None:
        self._c = client

    def _req(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Mapping[str, QueryValue]] = None,
        body: Any = None,
        headers: Optional[Mapping[str, str]] = None,
        sends_mail: bool = False,
        idempotent: bool = False,
        text: bool = False,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        return self._c._call(
            Call(
                method,
                path,
                query=query or {},
                body=body,
                headers=headers or {},
                sends_mail=sends_mail,
                idempotent=idempotent,
                text=text,
                idempotency_key=idempotency_key,
                timeout=timeout,
            )
        )


class Emails(_Resource):
    def send(
        self,
        email: SendEmailRequest,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> SendEmailResponse:
        """Sends one email (or schedules it with ``scheduled_at``)."""
        return cast(
            SendEmailResponse,
            self._req(
                "POST",
                "/v1/emails",
                body=email_body(email),
                sends_mail=True,
                idempotency_key=idempotency_key,
                timeout=timeout,
            ),
        )

    def batch(
        self,
        emails: Sequence[SendEmailRequest],
        *,
        batch_validation: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> BatchResponse:
        """Sends up to 100 emails in one request.

        ``batch_validation="strict"`` (default): one invalid item refuses the whole
        batch with ``invalid_batch``. ``"permissive"``: valid items send, invalid
        ones come back in ``errors``.
        """
        return cast(
            BatchResponse,
            self._req(
                "POST",
                "/v1/emails/batch",
                body=[email_body(e) for e in emails],
                headers={"x-batch-validation": batch_validation} if batch_validation else None,
                sends_mail=True,
                idempotency_key=idempotency_key,
                timeout=timeout,
            ),
        )

    def get(
        self, id: str, *, idempotency_key: Optional[str] = None, timeout: Optional[float] = None
    ) -> EmailDetail:
        return cast(
            EmailDetail,
            self._req(
                "GET",
                f"/v1/emails/{seg(id)}",
                idempotency_key=idempotency_key,
                timeout=timeout,
            ),
        )

    def list(
        self,
        *,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> EmailPage:
        return cast(
            EmailPage,
            self._req(
                "GET",
                "/v1/emails",
                query={"limit": limit, "cursor": cursor},
                idempotency_key=idempotency_key,
                timeout=timeout,
            ),
        )

    def update(
        self,
        id: str,
        *,
        scheduled_at: TimeInput,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> EmailRef:
        """Changes when a scheduled email sends."""
        return cast(
            EmailRef,
            self._req(
                "PATCH",
                f"/v1/emails/{seg(id)}",
                body={"scheduled_at": iso(scheduled_at)},
                idempotency_key=idempotency_key,
                timeout=timeout,
            ),
        )

    def reschedule(
        self,
        id: str,
        scheduled_at: TimeInput,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> EmailRef:
        """Shorthand for ``update(id, scheduled_at=...)``."""
        return self.update(
            id, scheduled_at=scheduled_at, idempotency_key=idempotency_key, timeout=timeout
        )

    def cancel(
        self, id: str, *, idempotency_key: Optional[str] = None, timeout: Optional[float] = None
    ) -> EmailRef:
        """Cancels a scheduled email."""
        return cast(
            EmailRef,
            self._req(
                "POST",
                f"/v1/emails/{seg(id)}/cancel",
                idempotent=True,
                idempotency_key=idempotency_key,
                timeout=timeout,
            ),
        )


class Senders(_Resource):
    def list(
        self, *, idempotency_key: Optional[str] = None, timeout: Optional[float] = None
    ) -> SenderList:
        """Addresses you can use as ``from``."""
        return cast(
            SenderList,
            self._req("GET", "/v1/senders", idempotency_key=idempotency_key, timeout=timeout),
        )


class Inboxes(_Resource):
    def create(
        self,
        inbox: Optional[CreateInboxRequest] = None,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Inbox:
        return cast(
            Inbox,
            self._req(
                "POST",
                "/v1/inboxes",
                body=dict(inbox or {}),
                idempotency_key=idempotency_key,
                timeout=timeout,
            ),
        )

    def list(
        self, *, idempotency_key: Optional[str] = None, timeout: Optional[float] = None
    ) -> InboxList:
        return cast(
            InboxList,
            self._req("GET", "/v1/inboxes", idempotency_key=idempotency_key, timeout=timeout),
        )

    def get(
        self, id: str, *, idempotency_key: Optional[str] = None, timeout: Optional[float] = None
    ) -> Inbox:
        return cast(
            Inbox,
            self._req(
                "GET", f"/v1/inboxes/{seg(id)}", idempotency_key=idempotency_key, timeout=timeout
            ),
        )

    def delete(
        self, id: str, *, idempotency_key: Optional[str] = None, timeout: Optional[float] = None
    ) -> None:
        self._req(
            "DELETE", f"/v1/inboxes/{seg(id)}", idempotency_key=idempotency_key, timeout=timeout
        )


class Messages(_Resource):
    def list(
        self,
        inbox_id: str,
        *,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> MessagePage:
        """Messages of an inbox, newest first."""
        return cast(
            MessagePage,
            self._req(
                "GET",
                f"/v1/inboxes/{seg(inbox_id)}/messages",
                query={"limit": limit, "cursor": cursor},
                idempotency_key=idempotency_key,
                timeout=timeout,
            ),
        )

    def get(
        self, id: str, *, idempotency_key: Optional[str] = None, timeout: Optional[float] = None
    ) -> Message:
        return cast(
            Message,
            self._req(
                "GET", f"/v1/messages/{seg(id)}", idempotency_key=idempotency_key, timeout=timeout
            ),
        )

    def raw(
        self, id: str, *, idempotency_key: Optional[str] = None, timeout: Optional[float] = None
    ) -> str:
        """The message as received (RFC 822)."""
        return cast(
            str,
            self._req(
                "GET",
                f"/v1/messages/{seg(id)}/raw",
                text=True,
                idempotency_key=idempotency_key,
                timeout=timeout,
            ),
        )

    def wait(
        self,
        inbox_id: str,
        *,
        from_: Optional[str] = None,
        subject: Optional[str] = None,
        to: Optional[str] = None,
        timeout: int = 30,
        after: Optional[TimeInput] = None,
        idempotency_key: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ) -> Optional[Waited]:
        """Blocks until a matching message arrives and returns it with the codes and links in it.

        ``from_`` (sent as ``from``) and ``subject`` are case insensitive substrings,
        ``to`` is a recipient address matched exactly, ``timeout`` is how many
        seconds to wait (1 to 120) and ``after`` only matches messages received after
        that time (default: when the wait starts). Returns ``None`` when nothing
        arrived in time; calling again is safe. ``request_timeout`` overrides the
        HTTP timeout, which defaults to ``timeout`` plus 15 seconds.
        """
        try:
            return cast(
                Waited,
                self._req(
                    "GET",
                    f"/v1/inboxes/{seg(inbox_id)}/wait",
                    query=wait_params(from_, subject, to, timeout, after),
                    idempotency_key=idempotency_key,
                    timeout=timeout + 15 if request_timeout is None else request_timeout,
                ),
            )
        except RunearcError as e:
            if e.code == "wait_timeout":
                return None
            raise

    def send(
        self,
        inbox_id: str,
        message: SendMessageRequest,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Message:
        """Sends a new message from an inbox."""
        return cast(
            Message,
            self._req(
                "POST",
                f"/v1/inboxes/{seg(inbox_id)}/messages",
                body=message,
                sends_mail=True,
                idempotency_key=idempotency_key,
                timeout=timeout,
            ),
        )

    def reply(
        self,
        message_id: str,
        reply: ReplyRequest,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Message:
        """Replies in the thread of a message."""
        return cast(
            Message,
            self._req(
                "POST",
                f"/v1/messages/{seg(message_id)}/reply",
                body=reply,
                sends_mail=True,
                idempotency_key=idempotency_key,
                timeout=timeout,
            ),
        )


class Threads(_Resource):
    def list(
        self,
        inbox_id: str,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> ThreadList:
        return cast(
            ThreadList,
            self._req(
                "GET",
                f"/v1/inboxes/{seg(inbox_id)}/threads",
                idempotency_key=idempotency_key,
                timeout=timeout,
            ),
        )

    def get(
        self, id: str, *, idempotency_key: Optional[str] = None, timeout: Optional[float] = None
    ) -> ThreadWithMessages:
        return cast(
            ThreadWithMessages,
            self._req(
                "GET", f"/v1/threads/{seg(id)}", idempotency_key=idempotency_key, timeout=timeout
            ),
        )


class Conversations(_Resource):
    def list(
        self,
        *,
        status: Optional[str] = None,
        assignee: Optional[str] = None,
        project_id: Optional[str] = None,
        inbox_id: Optional[str] = None,
        q: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> ConversationPage:
        """``status`` is ``open``, ``closed``, ``snoozed`` or ``all``; ``assignee`` is
        ``me``, ``none`` or a user id."""
        return cast(
            ConversationPage,
            self._req(
                "GET",
                "/v1/conversations",
                query={
                    "status": status,
                    "assignee": assignee,
                    "project_id": project_id,
                    "inbox_id": inbox_id,
                    "q": q,
                    "cursor": cursor,
                    "limit": limit,
                },
                idempotency_key=idempotency_key,
                timeout=timeout,
            ),
        )

    def counts(
        self, *, idempotency_key: Optional[str] = None, timeout: Optional[float] = None
    ) -> ConversationCounts:
        return cast(
            ConversationCounts,
            self._req(
                "GET", "/v1/conversations/counts", idempotency_key=idempotency_key, timeout=timeout
            ),
        )

    def update(
        self,
        id: str,
        update: ConversationUpdate,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Conversation:
        body: dict[str, Any] = dict(update)
        if isinstance(body.get("snoozed_until"), datetime):
            body["snoozed_until"] = iso(body["snoozed_until"])
        return cast(
            Conversation,
            self._req(
                "PATCH",
                f"/v1/conversations/{seg(id)}",
                body=body,
                idempotency_key=idempotency_key,
                timeout=timeout,
            ),
        )

    def read(
        self, id: str, *, idempotency_key: Optional[str] = None, timeout: Optional[float] = None
    ) -> None:
        self._req(
            "POST",
            f"/v1/conversations/{seg(id)}/read",
            idempotent=True,
            idempotency_key=idempotency_key,
            timeout=timeout,
        )

    def unread(
        self, id: str, *, idempotency_key: Optional[str] = None, timeout: Optional[float] = None
    ) -> None:
        self._req(
            "DELETE",
            f"/v1/conversations/{seg(id)}/read",
            idempotency_key=idempotency_key,
            timeout=timeout,
        )


class Domains(_Resource):
    def create(
        self,
        name: str,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> AddedDomain:
        return cast(
            AddedDomain,
            self._req(
                "POST",
                "/v1/domains",
                body={"name": name},
                idempotency_key=idempotency_key,
                timeout=timeout,
            ),
        )

    def list(
        self, *, idempotency_key: Optional[str] = None, timeout: Optional[float] = None
    ) -> DomainList:
        return cast(
            DomainList,
            self._req("GET", "/v1/domains", idempotency_key=idempotency_key, timeout=timeout),
        )

    def get(
        self, id: str, *, idempotency_key: Optional[str] = None, timeout: Optional[float] = None
    ) -> Domain:
        """``id`` is a domain id (``dom_...``) or the domain name."""
        return cast(
            Domain,
            self._req(
                "GET", f"/v1/domains/{seg(id)}", idempotency_key=idempotency_key, timeout=timeout
            ),
        )

    def delete(
        self, id: str, *, idempotency_key: Optional[str] = None, timeout: Optional[float] = None
    ) -> None:
        self._req(
            "DELETE", f"/v1/domains/{seg(id)}", idempotency_key=idempotency_key, timeout=timeout
        )

    def verify(
        self, id: str, *, idempotency_key: Optional[str] = None, timeout: Optional[float] = None
    ) -> Domain:
        """Checks the DNS records now and returns the domain with each record's status."""
        return cast(
            Domain,
            self._req(
                "POST",
                f"/v1/domains/{seg(id)}/verify",
                idempotent=True,
                idempotency_key=idempotency_key,
                timeout=timeout,
            ),
        )


class Webhooks(_Resource):
    def create(
        self,
        webhook: CreateWebhookRequest,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> CreatedWebhook:
        """The response has the signing ``secret``, shown once."""
        return cast(
            CreatedWebhook,
            self._req(
                "POST",
                "/v1/webhooks",
                body=webhook,
                idempotency_key=idempotency_key,
                timeout=timeout,
            ),
        )

    def list(
        self, *, idempotency_key: Optional[str] = None, timeout: Optional[float] = None
    ) -> WebhookList:
        return cast(
            WebhookList,
            self._req("GET", "/v1/webhooks", idempotency_key=idempotency_key, timeout=timeout),
        )

    def delete(
        self, id: str, *, idempotency_key: Optional[str] = None, timeout: Optional[float] = None
    ) -> None:
        self._req(
            "DELETE", f"/v1/webhooks/{seg(id)}", idempotency_key=idempotency_key, timeout=timeout
        )

    def deliveries(
        self,
        id: str,
        *,
        limit: Optional[int] = None,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> DeliveryList:
        return cast(
            DeliveryList,
            self._req(
                "GET",
                f"/v1/webhooks/{seg(id)}/deliveries",
                query={"limit": limit},
                idempotency_key=idempotency_key,
                timeout=timeout,
            ),
        )

    def replay(
        self,
        id: str,
        delivery_id: str,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Delivery:
        """Delivers the event of a past delivery again."""
        return cast(
            Delivery,
            self._req(
                "POST",
                f"/v1/webhooks/{seg(id)}/deliveries/{seg(delivery_id)}/replay",
                idempotency_key=idempotency_key,
                timeout=timeout,
            ),
        )

    def verify(
        self,
        payload: Union[str, bytes],
        headers: Mapping[str, Any],
        secret: str,
        *,
        tolerance_seconds: int = WEBHOOK_TOLERANCE_SECONDS,
        now: Optional[float] = None,
    ) -> WebhookEvent:
        """Same as the standalone ``verify_webhook``."""
        return verify_webhook(
            payload, headers, secret, tolerance_seconds=tolerance_seconds, now=now
        )


class Keys(_Resource):
    def create(
        self,
        key: CreateKeyRequest,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> CreatedKey:
        """The response has the key's ``secret``, shown once."""
        return cast(
            CreatedKey,
            self._req(
                "POST", "/v1/keys", body=key, idempotency_key=idempotency_key, timeout=timeout
            ),
        )

    def list(
        self, *, idempotency_key: Optional[str] = None, timeout: Optional[float] = None
    ) -> KeyList:
        return cast(
            KeyList,
            self._req("GET", "/v1/keys", idempotency_key=idempotency_key, timeout=timeout),
        )

    def delete(
        self, id: str, *, idempotency_key: Optional[str] = None, timeout: Optional[float] = None
    ) -> None:
        """Revokes the key at once."""
        self._req("DELETE", f"/v1/keys/{seg(id)}", idempotency_key=idempotency_key, timeout=timeout)


class Suppressions(_Resource):
    def list(
        self, *, idempotency_key: Optional[str] = None, timeout: Optional[float] = None
    ) -> SuppressionList:
        return cast(
            SuppressionList,
            self._req("GET", "/v1/suppressions", idempotency_key=idempotency_key, timeout=timeout),
        )

    def delete(
        self,
        address: str,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        """Lets mail go to the address again."""
        self._req(
            "DELETE",
            f"/v1/suppressions/{seg(address)}",
            idempotency_key=idempotency_key,
            timeout=timeout,
        )


class Events(_Resource):
    def list(
        self,
        *,
        after: Optional[str] = None,
        limit: Optional[int] = None,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> EventPage:
        """Events oldest first. Pass the returned ``next_after`` as ``after`` to continue."""
        return cast(
            EventPage,
            self._req(
                "GET",
                "/v1/events",
                query={"after": after, "limit": limit},
                idempotency_key=idempotency_key,
                timeout=timeout,
            ),
        )
