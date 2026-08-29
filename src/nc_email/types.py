"""Request parameter shapes and response objects.

Two different jobs, deliberately spelled two different ways:

* Parameters are `TypedDict`s. A caller building a message from a config file or
  a database row already has a dict; making them construct an object first would
  buy nothing, and `send(**row)` is the shape people reach for.
* Responses are frozen dataclasses with a `from_dict` that ignores unknown keys.
  Frozen because a response is a record of what the server said and mutating it
  only ever hides a bug. Ignoring unknown keys because the server ships
  independently of this package: a field added next month must not break every
  pinned SDK in production.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, TypedDict, Union

__all__ = [
    "Recipients",
    "AttachmentParam",
    "SendParams",
    "MessageStatus",
    "MESSAGE_STATUSES",
    "RejectedRecipient",
    "SendEmailResponse",
    "Email",
    "WebhookEvent",
]

#: One address or several. Both shapes are accepted everywhere the API takes
#: recipients; this SDK always sends the array form so the wire is predictable.
Recipients = Union[str, List[str]]


class AttachmentParam(TypedDict, total=False):
    """`filename` and `content` are required in practice; enforced at runtime.

    The camelCase spellings are accepted alongside the snake_case ones because
    someone porting from a JavaScript SDK will have them already.
    """

    filename: str
    #: bytes is the supported path — the SDK base64-encodes it. A str is read as
    #: already-encoded base64 and validated strictly; see emails.py.
    content: Union[bytes, bytearray, str]
    content_type: str
    contentType: str
    content_id: str
    contentId: str


# Functional syntax because `from` is a Python keyword and cannot be a class
# attribute. Every key is optional at the type level and required-ness is
# enforced at runtime, since `from`/`from_` are alternatives for one field and
# no TypedDict can express "exactly one of these two".
SendParams = TypedDict(
    "SendParams",
    {
        "from": str,
        "from_": str,
        "to": Recipients,
        "cc": Recipients,
        "bcc": Recipients,
        "reply_to": Recipients,
        "replyTo": Recipients,
        "subject": str,
        "html": str,
        "text": str,
        "headers": Mapping[str, str],
        "attachments": List[AttachmentParam],
        "tags": Mapping[str, str],
        "idempotency_key": str,
    },
    total=False,
)


class MessageStatus:
    """The statuses the API sends today, as plain strings.

    Not an `enum.Enum`: `MessageStatus("shredded")` would raise, so the day the
    platform adds a status every deployed SDK older than that day starts
    throwing on a perfectly good response. Responses therefore carry the raw
    string and these constants exist for comparison, with `is_known()` for code
    that wants to branch on "something new arrived".

    The order below reads like a progression but is not a state machine: a
    message can go `delivered` then `complained`, and providers deliver events
    out of order often enough that no client should assume otherwise.
    """

    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    BOUNCED = "bounced"
    DEFERRED = "deferred"
    COMPLAINED = "complained"
    REJECTED = "rejected"
    FAILED = "failed"

    KNOWN = frozenset(
        {QUEUED, SENT, DELIVERED, BOUNCED, DEFERRED, COMPLAINED, REJECTED, FAILED}
    )

    @staticmethod
    def is_known(value: str) -> bool:
        return value in MessageStatus.KNOWN


#: Module-level alias, for `if status in MESSAGE_STATUSES:`.
MESSAGE_STATUSES = MessageStatus.KNOWN


def _str(data: Mapping[str, Any], key: str, default: str = "") -> str:
    value = data.get(key, default)
    return value if isinstance(value, str) else default


def _opt_str(data: Mapping[str, Any], key: str) -> Optional[str]:
    value = data.get(key)
    return value if isinstance(value, str) else None


def _bool(data: Mapping[str, Any], key: str) -> bool:
    return bool(data.get(key, False))


@dataclass(frozen=True)
class RejectedRecipient:
    """An address we refused — today always because it is on the suppression list.

    Not an error. The rest of the message still went out, which is why it rides
    on a successful response instead of raising.
    """

    address: str
    reason: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RejectedRecipient:
        return cls(address=_str(data, "address"), reason=_str(data, "reason"))


@dataclass(frozen=True)
class SendEmailResponse:
    """A `202 Accepted`.

    Accepted means queued after authorisation, suppression, reputation and quota
    checks — not that a mailbox has it. `id` is our message id, stable across a
    delivery-backend change; one row exists per primary recipient and this is the
    first of them.
    """

    id: str
    status: str
    #: The server omits this key entirely when nothing was rejected. It is
    #: normalised to an empty list so callers never write `if resp.rejected is
    #: not None and resp.rejected`.
    rejected: List[RejectedRecipient] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SendEmailResponse:
        raw = data.get("rejected")
        rejected = [
            RejectedRecipient.from_dict(item)
            for item in (raw if isinstance(raw, list) else [])
            if isinstance(item, Mapping)
        ]
        return cls(id=_str(data, "id"), status=_str(data, "status"), rejected=rejected)


@dataclass(frozen=True)
class Email:
    """One message's current state.

    `from_` because `from` is reserved. `to` is a single address: the server
    writes one record per primary recipient, so a three-recipient send produces
    three of these and returns the id of the first.

    Timestamps stay as the ISO-8601 strings the server sent. Parsing them here
    would mean guessing at a format we do not control, and a lenient parse that
    quietly returns the wrong instant is worse than handing the caller the text.
    """

    id: str
    to: str
    from_: str
    subject: str
    status: str
    created_at: str
    delivered_at: Optional[str] = None
    opened: bool = False
    clicked: bool = False
    #: Present only when the message failed.
    failure_reason: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Email:
        return cls(
            id=_str(data, "id"),
            to=_str(data, "to"),
            from_=_str(data, "from"),
            subject=_str(data, "subject"),
            status=_str(data, "status"),
            created_at=_str(data, "created_at"),
            delivered_at=_opt_str(data, "delivered_at"),
            opened=_bool(data, "opened"),
            clicked=_bool(data, "clicked"),
            failure_reason=_opt_str(data, "failure_reason"),
        )


@dataclass(frozen=True)
class WebhookEvent:
    """A verified webhook payload.

    `data` and `raw` are kept deliberately loose. The event schema is fixed in
    SDK-CONTRACT.md section 6 but the control plane does not emit these yet, so
    pinning field names here would be inventing an API that could not be
    corrected without a breaking change. `raw` is always the whole decoded body.
    """

    type: str
    id: Optional[str] = None
    created_at: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> WebhookEvent:
        payload = data.get("data")
        return cls(
            type=_str(data, "type"),
            id=_opt_str(data, "id"),
            created_at=_opt_str(data, "created_at"),
            data=dict(payload) if isinstance(payload, Mapping) else {},
            raw=dict(data),
        )
