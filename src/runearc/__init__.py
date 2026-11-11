"""The official Python SDK for the Runearc email API."""

from . import types
from ._http import DEFAULT_BASE_URL, VERSION
from .async_client import AsyncRunearc
from .client import Runearc
from .errors import RunearcError, WebhookVerificationError
from .webhooks import WEBHOOK_TOLERANCE_SECONDS, verify_webhook

__version__ = VERSION

__all__ = [
    "DEFAULT_BASE_URL",
    "VERSION",
    "WEBHOOK_TOLERANCE_SECONDS",
    "AsyncRunearc",
    "Runearc",
    "RunearcError",
    "WebhookVerificationError",
    "__version__",
    "types",
    "verify_webhook",
]
