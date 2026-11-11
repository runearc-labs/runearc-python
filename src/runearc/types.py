"""Request and response shapes of the Runearc REST API.

Field names are the API's JSON (snake_case), so what you read here is what goes
over the wire. Responses are plain ``dict`` objects typed as ``TypedDict``.
Keys that may be missing are declared in a ``total=False`` subclass.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, TypedDict, Union

Timestamp = str
"""An RFC 3339 time, such as ``2026-10-20T09:00:00Z``."""

Recipients = Union[str, List[str]]
"""One address or many. ``"Name <addr>"`` or ``addr``."""

TimeInput = Union[str, datetime]
"""An RFC 3339 string, or a ``datetime`` (a naive one is taken as local time)."""


# ---------------------------------------------------------------------------
# Emails


class EmailAttachmentInput(TypedDict, total=False):
    filename: str
    content: str
    """Base64 (standard alphabet)."""
    content_type: str
    """Derived from the filename when absent."""
    content_id: str
    """Makes the attachment an inline image, referenced as ``cid:<content_id>``."""


class Tag(TypedDict):
    name: str
    value: str


_SendEmailRequired = TypedDict(
    "_SendEmailRequired", {"from": str, "to": Recipients, "subject": str}
)


class SendEmailRequest(_SendEmailRequired, total=False):
    """``POST /v1/emails``, shaped like Resend's ``POST /emails``.

    ``from`` is ``"Acme <noreply@acme.com>"`` or ``noreply@acme.com``, on a
    verified domain. ``scheduled_at`` sends later: RFC 3339 with an offset, a
    ``datetime``, or Resend's relative form (``in 1 hour``), at most 30 days ahead.
    """

    cc: Recipients
    bcc: Recipients
    reply_to: Recipients
    html: str
    text: str
    headers: Dict[str, str]
    attachments: List[EmailAttachmentInput]
    tags: List[Tag]
    scheduled_at: TimeInput


class SendEmailResponse(TypedDict):
    id: str


class EmailRecipient(TypedDict):
    address: str
    kind: str
    """``to``, ``cc`` or ``bcc``."""
    status: str
    response: Optional[str]
    """The receiving server's answer, such as ``550 5.1.1 no such user``."""
    updated_at: Timestamp


class EmailAttachment(TypedDict):
    id: str
    filename: str
    content_type: str
    content_id: Optional[str]
    size_bytes: int


_EmailRequired = TypedDict(
    "_EmailRequired",
    {
        "id": str,
        "project_id": str,
        "domain_id": Optional[str],
        "from": str,
        "from_name": Optional[str],
        "to": List[str],
        "cc": List[str],
        "bcc": List[str],
        "reply_to": List[str],
        "subject": str,
        "status": str,
        "tags": List[Tag],
        "size_bytes": int,
        "created_at": Timestamp,
    },
)


class Email(_EmailRequired, total=False):
    """``status`` is ``scheduled``, ``queued``, ``sent``, ``delivered``, ``deferred``,
    ``bounced``, ``failed``, ``complained`` or ``canceled``."""

    scheduled_at: Optional[Timestamp]
    """When the email is (or was) scheduled to send, or ``None``."""


class EmailDetail(Email):
    object: str
    last_event: str
    """Resend's name for the latest status."""
    html: Optional[str]
    text: Optional[str]
    headers: Dict[str, str]
    attachments: List[EmailAttachment]
    recipients: List[EmailRecipient]


class UpdateEmailRequest(TypedDict):
    scheduled_at: TimeInput


class EmailRef(TypedDict):
    object: str
    id: str


class _BatchItemErrorRequired(TypedDict):
    index: int
    """0 based."""
    code: str
    message: str


class BatchItemError(_BatchItemErrorRequired, total=False):
    fix: Optional[str]


class EmailId(TypedDict):
    id: str


class _BatchResponseRequired(TypedDict):
    data: List[EmailId]
    """The ids of the emails accepted, in request order."""


class BatchResponse(_BatchResponseRequired, total=False):
    errors: List[BatchItemError]
    """Only with ``batch_validation="permissive"``: the items that were refused."""


class EmailPage(TypedDict):
    data: List[Email]
    next_cursor: Optional[str]
    """Pass as ``cursor`` to get the next page; ``None`` on the last page."""


class SenderAddress(TypedDict):
    address: str
    """``noreply@acme.com``, or ``*@acme.com`` for any address on a verified domain."""
    domain: str
    domain_id: str
    verified: bool
    kind: str
    """``sender`` or ``domain``."""
    display_name: Optional[str]


class SenderList(TypedDict):
    data: List[SenderAddress]


# ---------------------------------------------------------------------------
# Inboxes, messages, threads


class Inbox(TypedDict):
    id: str
    org_id: str
    project_id: str
    address: str
    local_part: str
    domain: str
    display_name: Optional[str]
    catch_all: bool
    created_at: Timestamp


class InboxList(TypedDict):
    data: List[Inbox]


class CreateInboxRequest(TypedDict, total=False):
    local_part: str
    """Random when absent."""
    display_name: str
    domain: str
    """A custom domain of the workspace; the shared domain when absent."""


class MessageAttachment(TypedDict):
    id: str
    org_id: str
    message_id: str
    filename: Optional[str]
    content_type: str
    size_bytes: int
    created_at: Timestamp


_MessageFields = TypedDict(
    "_MessageFields",
    {
        "id": str,
        "org_id": str,
        "project_id": str,
        "inbox_id": str,
        "thread_id": str,
        "direction": str,
        "rfc_message_id": Optional[str],
        "in_reply_to": Optional[str],
        "references": List[str],
        "from": str,
        "to": List[str],
        "cc": List[str],
        "delivered_to": List[str],
        "subject": str,
        "text": Optional[str],
        "html": Optional[str],
        "size_bytes": int,
        "status": str,
        "spam": bool,
        "auth_results": Any,
        "created_at": Timestamp,
        "attachments": List[MessageAttachment],
    },
)


class Message(_MessageFields):
    """``direction`` is ``inbound`` or ``outbound``."""


class MessagePage(TypedDict):
    data: List[Message]
    next_cursor: Optional[str]


class _AttachmentInputRequired(TypedDict):
    filename: str
    content: str
    """Base64 (standard alphabet)."""


class AttachmentInput(_AttachmentInputRequired, total=False):
    content_type: str
    """Defaults to ``application/octet-stream``."""


class _SendMessageRequired(TypedDict):
    to: Recipients
    subject: str


class SendMessageRequest(_SendMessageRequired, total=False):
    cc: Recipients
    bcc: Recipients
    text: str
    html: str
    reply_to: Recipients
    headers: Dict[str, str]
    attachments: List[AttachmentInput]


class ReplyRequest(TypedDict, total=False):
    text: str
    html: str
    reply_all: bool
    """Also copy everyone else on the original message."""
    headers: Dict[str, str]
    attachments: List[AttachmentInput]


class Waited(TypedDict):
    message: Message
    codes: List[str]
    """One time codes found in the subject and body."""
    links: List[str]


class Thread(TypedDict):
    id: str
    org_id: str
    project_id: str
    inbox_id: str
    subject: str
    last_message_at: Timestamp
    message_count: int
    created_at: Timestamp
    status: str
    assignee_user_id: Optional[str]
    snoozed_until: Optional[Timestamp]
    last_inbound_at: Optional[Timestamp]


class ThreadList(TypedDict):
    data: List[Thread]


class ThreadWithMessages(Thread):
    messages: List[Message]


# ---------------------------------------------------------------------------
# Conversations


class ConversationProject(TypedDict):
    id: str
    name: str


class ConversationInbox(TypedDict):
    id: str
    address: str


class ConversationAssignee(TypedDict):
    id: str
    email: str


class Conversation(TypedDict):
    id: str
    """The thread id."""
    project: ConversationProject
    inbox: ConversationInbox
    subject: str
    snippet: str
    status: str
    snoozed_until: Optional[Timestamp]
    assignee: Optional[ConversationAssignee]
    unread: bool
    message_count: int
    last_message_at: Timestamp
    last_inbound_at: Optional[Timestamp]
    last_from: str


class ConversationPage(TypedDict):
    data: List[Conversation]
    next_cursor: Optional[str]


class ConversationUpdate(TypedDict, total=False):
    status: str
    """``open`` or ``closed``."""
    assignee_user_id: Optional[str]
    """``None`` unassigns."""
    snoozed_until: Optional[TimeInput]
    """``None`` wakes the conversation."""


class ProjectCount(TypedDict):
    project_id: str
    open: int
    unread: int


class InboxCount(TypedDict):
    inbox_id: str
    project_id: str
    open: int
    unread: int


class ConversationCounts(TypedDict):
    open: int
    assigned_to_me: int
    unassigned: int
    by_project: List[ProjectCount]
    by_inbox: List[InboxCount]


# ---------------------------------------------------------------------------
# Domains


class DomainRecord(TypedDict):
    kind: str
    name: str
    type: str
    value: str
    priority: Optional[int]
    required: bool
    status: str
    """``unchecked``, ``ok``, ``missing`` or ``wrong``."""
    fix: Optional[str]
    last_checked_at: Optional[Timestamp]


class Domain(TypedDict):
    id: str
    org_id: str
    project_id: str
    name: str
    parent_domain_id: Optional[str]
    status: str
    """``pending``, ``verified`` or ``failing``."""
    provider: Optional[str]
    can_auto_configure: bool
    dkim_selector: str
    verified_at: Optional[Timestamp]
    last_checked_at: Optional[Timestamp]
    created_at: Timestamp
    records: List[DomainRecord]
    next_step: str
    """The one thing to do next."""


class DomainList(TypedDict):
    data: List[Domain]


class ExistingMx(TypedDict):
    hosts: List[str]
    suggestion: str
    message: str


class AddedDomain(Domain):
    existing_mx: Optional[ExistingMx]


# ---------------------------------------------------------------------------
# Webhooks and events


class Webhook(TypedDict):
    id: str
    org_id: str
    project_id: str
    url: str
    event_types: List[str]
    """Empty means every event type."""
    inbox_id: Optional[str]
    """``None`` means every inbox in the project."""
    created_at: Timestamp


class WebhookList(TypedDict):
    data: List[Webhook]


class CreatedWebhook(Webhook):
    secret: str
    """``whsec_...``. Shown once: store it to verify signatures."""


class _CreateWebhookRequired(TypedDict):
    url: str


class CreateWebhookRequest(_CreateWebhookRequired, total=False):
    event_types: List[str]
    inbox_id: Optional[str]


class Delivery(TypedDict):
    id: str
    webhook_id: str
    event_id: str
    replay_of: Optional[str]
    attempt: int
    status: str
    response_code: Optional[int]
    response_body_snippet: Optional[str]
    next_attempt_at: Optional[Timestamp]
    created_at: Timestamp
    updated_at: Timestamp


class DeliveryList(TypedDict):
    data: List[Delivery]


class Event(TypedDict):
    id: str
    org_id: str
    project_id: str
    type: str
    data: Dict[str, Any]
    created_at: Timestamp


class EventPage(TypedDict):
    data: List[Event]
    next_after: Optional[str]
    """Pass as ``after`` to get the next events."""


class WebhookEvent(TypedDict):
    """The JSON body Runearc POSTs to a webhook endpoint."""

    id: str
    type: str
    timestamp: Timestamp
    data: Dict[str, Any]


# ---------------------------------------------------------------------------
# Keys and suppressions


class ApiKey(TypedDict):
    id: str
    org_id: str
    project_id: str
    name: str
    prefix: str
    scope: str
    permissions: str
    """``full``, ``sending`` or ``inbox``."""
    domain_id: Optional[str]
    last_used_at: Optional[Timestamp]
    created_at: Timestamp


class KeyList(TypedDict):
    data: List[ApiKey]


class _CreateKeyRequired(TypedDict):
    name: str


class CreateKeyRequest(_CreateKeyRequired, total=False):
    permissions: str
    """``full`` (default), ``sending`` or ``inbox``."""
    inbox_id: str
    """Inbox keys: the one inbox."""
    domain_id: str
    """Sending keys: limit to one domain (id or name)."""


class CreatedKey(TypedDict):
    key: ApiKey
    secret: str
    """Shown once; Runearc stores only a hash."""


class Suppression(TypedDict):
    address: str
    reason: str
    created_at: Timestamp


class SuppressionList(TypedDict):
    data: List[Suppression]
