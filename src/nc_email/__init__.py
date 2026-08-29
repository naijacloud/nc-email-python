"""Naijamail — the official Python SDK for Naija Cloud's transactional email API.

    from nc_email import Naijamail

    nm = Naijamail()                      # reads NAIJAMAIL_API_KEY
    sent = nm.emails.send(
        from_="Acme <hello@acme.com>",
        to="customer@example.com",
        subject="Your receipt",
        html="<p>Thanks for your order.</p>",
    )
    print(sent.id, sent.status)

The package holds a live sending credential, so it has zero runtime
dependencies: every third-party import would be another supply chain with the
ability to mail as a customer's verified domain.
"""

from __future__ import annotations

from ._version import __version__
from .client import (
    API_KEY_ENV_VAR,
    BASE_URL_ENV_VAR,
    DEFAULT_BASE_URL,
    Naijamail,
    redact_key,
)
from .emails import Emails
from .errors import (
    AuthenticationError,
    ConflictError,
    # The redundant `X as X` aliases mark these three as deliberate re-exports
    # rather than unused imports. They are absent from __all__ on purpose; see
    # the note below.
    ConnectionError as ConnectionError,
    NaijamailConnectionError,
    NaijamailError,
    NaijamailPermissionError,
    NaijamailTimeoutError,
    NotFoundError,
    PermissionError as PermissionError,
    RateLimitError,
    ServerError,
    TimeoutError as TimeoutError,
    ValidationError,
    WebhookVerificationError,
)
from .types import (
    MESSAGE_STATUSES,
    AttachmentParam,
    Email,
    MessageStatus,
    RejectedRecipient,
    SendEmailResponse,
    SendParams,
    WebhookEvent,
)
from .webhooks import DEFAULT_TOLERANCE_SECONDS, Webhooks, verify_webhook

# `ConnectionError`, `TimeoutError` and `PermissionError` are importable by name
# (`from nc_email import TimeoutError`) but are deliberately left out of __all__:
# a `from nc_email import *` that replaced those builtins in the importing module
# would silently change the meaning of unrelated `except` clauses somewhere else
# in that file. The Naijamail-prefixed spellings are the canonical ones.
__all__ = [
    "__version__",
    "API_KEY_ENV_VAR",
    "BASE_URL_ENV_VAR",
    "DEFAULT_BASE_URL",
    "DEFAULT_TOLERANCE_SECONDS",
    "MESSAGE_STATUSES",
    "AttachmentParam",
    "AuthenticationError",
    "ConflictError",
    "Email",
    "Emails",
    "MessageStatus",
    "Naijamail",
    "NaijamailConnectionError",
    "NaijamailError",
    "NaijamailPermissionError",
    "NaijamailTimeoutError",
    "NotFoundError",
    "RateLimitError",
    "RejectedRecipient",
    "SendEmailResponse",
    "SendParams",
    "ServerError",
    "ValidationError",
    "WebhookEvent",
    "WebhookVerificationError",
    "Webhooks",
    "redact_key",
    "verify_webhook",
]
