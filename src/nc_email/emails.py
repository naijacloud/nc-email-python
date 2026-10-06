"""The `emails` resource: send one message, retrieve one message.

Two methods, because the API has two endpoints. There is no domains, api-keys,
batch or contacts resource here — the control plane does not serve them, and an
SDK method that 404s is worse than no method at all.

Most of this file is validation that runs before anything touches a socket. That
is not defensive padding: a CRLF in a subject is a header-injection attempt, and
a caller who learns about it from a 400 raised by a machine they cannot see
debugs for an hour longer than one who gets a local exception naming the field.
"""

from __future__ import annotations

import base64
import difflib
import re
import urllib.parse
import uuid
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .errors import ServerError, ValidationError
from .http import Transport, encode_json
from .types import Email, SendEmailResponse, SendParams

#: Mirrors SENDING_LIMITS in nc-control-plane/src/mail/mail.constants.ts. Checked
#: here so a caller fails in milliseconds instead of after a round trip that was
#: never going to succeed.
MAX_RECIPIENTS = 50
MAX_BYTES = 10 * 1024 * 1024
MAX_HEADERS = 25
MAX_TAGS = 10
MAX_TAG_KEY_LENGTH = 64
MAX_TAG_VALUE_LENGTH = 256
#: Counted in bytes of UTF-8, the way the server stores it — not characters.
MAX_IDEMPOTENCY_KEY_BYTES = 255
#: Kept for callers who imported the old name; same value, now bytes.
MAX_IDEMPOTENCY_KEY_LENGTH = MAX_IDEMPOTENCY_KEY_BYTES

#: Overriding any of these would let a caller set a From/To/Subject the server
#: never authorised, sidestepping the domain check the real From is measured
#: against. The server refuses them too; refusing here as well means the caller
#: gets a clear local error instead of a 400.
FORBIDDEN_HEADERS = frozenset(
    {"from", "to", "cc", "bcc", "subject", "dkim-signature", "received"}
)

#: CR and LF end a header; NUL truncates one in some MTAs. Any of the three in a
#: value that becomes a header is an injection primitive.
_CONTROL_CHARS = ("\r", "\n", "\0")

_BASE64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")


def _reject_control_chars(value: str, label: str) -> str:
    for char in _CONTROL_CHARS:
        if char in value:
            raise ValidationError(
                "{} must not contain carriage returns, newlines or NUL bytes".format(label)
            )
    return value


def _require_str(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValidationError("{} must be a string, got {}".format(label, type(value).__name__))
    return value


def _normalise_recipients(value: Any, label: str) -> List[str]:
    """Accept a string or a sequence of them; always emit a list.

    The API takes both shapes, but sending one shape consistently means the wire
    format does not depend on how the caller happened to type the argument —
    which is one fewer difference between a working request and a broken one.
    """
    if value is None:
        return []
    items: Sequence[Any]
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        raise ValidationError(
            "{} must be a string or a list of strings, got {}".format(
                label, type(value).__name__
            )
        )

    # A caller who passed one string should read "to", not "to[0]".
    single = isinstance(value, str)
    addresses: List[str] = []
    for index, item in enumerate(items):
        field = label if single else "{}[{}]".format(label, index)
        address = _require_str(item, field)
        _reject_control_chars(address, field)
        if not address.strip():
            raise ValidationError("{} must not be empty".format(field))
        addresses.append(address)
    return addresses


def _encode_attachment_content(value: Any, label: str) -> Tuple[str, int]:
    """Bytes in, base64 on the wire.

    A caller who hand-encodes is a caller who eventually gets it subtly wrong,
    so the normal path is raw bytes and this function does the encoding. A `str`
    is taken as already-encoded base64 and validated with the same strictness
    the server applies, because Python's decoder — like Node's — silently
    discards characters outside the alphabet, and a silently mangled invoice is
    far worse than a rejected one.

    A file *path* is never accepted. An SDK that opens arbitrary paths on the
    caller's behalf is a local-file-read primitive the moment one of those paths
    comes from an HTTP request.

    Returns the base64 text and the decoded size, which is what the size limit
    is measured on.
    """
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        if not raw:
            raise ValidationError("{} content is empty".format(label))
        return base64.b64encode(raw).decode("ascii"), len(raw)

    if isinstance(value, str):
        compact = "".join(value.split())  # line wrapping is legal in base64
        if not compact:
            raise ValidationError("{} content is empty".format(label))
        if len(compact) % 4 or not _BASE64_RE.match(compact):
            raise ValidationError(
                "{} content is a string but is not valid base64. Pass the raw bytes "
                "and the SDK will encode them.".format(label)
            )
        try:
            decoded = base64.b64decode(compact, validate=True)
        except (ValueError, TypeError):
            # `from None`: binascii's own message adds nothing the caller can
            # act on, and the chained traceback buries the sentence that can.
            raise ValidationError(
                "{} content is a string but is not valid base64. Pass the raw bytes "
                "and the SDK will encode them.".format(label)
            ) from None
        if not decoded:
            raise ValidationError("{} content is empty".format(label))
        return compact, len(decoded)

    raise ValidationError(
        "{} content must be bytes (preferred) or a base64 string, got {}".format(
            label, type(value).__name__
        )
    )


def _build_attachments(value: Any) -> Tuple[List[Dict[str, str]], int]:
    """The wire attachments, and their total decoded size in bytes."""
    if value is None:
        return [], 0
    if not isinstance(value, (list, tuple)):
        raise ValidationError("attachments must be a list")

    built: List[Dict[str, str]] = []
    total = 0
    for index, item in enumerate(value):
        label = "attachments[{}]".format(index)
        if not isinstance(item, Mapping):
            raise ValidationError("{} must be a mapping".format(label))
        if "path" in item:
            # Resend's Python SDK accepts `path`; someone porting will send it.
            # Say why it is gone rather than dropping the attachment silently.
            raise ValidationError(
                "{} 'path' is not supported: this SDK never opens files on your "
                "behalf. Read the file yourself and pass the bytes as 'content'.".format(label)
            )
        if "filename" not in item or "content" not in item:
            raise ValidationError("{} needs a filename and content".format(label))

        filename = _require_str(item["filename"], "{} filename".format(label))
        _reject_control_chars(filename, "{} filename".format(label))
        if not filename.strip():
            raise ValidationError("{} filename must not be empty".format(label))

        encoded, size = _encode_attachment_content(item["content"], label)
        total += size
        attachment: Dict[str, str] = {"filename": filename, "content": encoded}
        content_type = item.get("content_type", item.get("contentType"))
        if content_type is not None:
            attachment["content_type"] = _reject_control_chars(
                _require_str(content_type, "{} content_type".format(label)),
                "{} content_type".format(label),
            )
        content_id = item.get("content_id", item.get("contentId"))
        if content_id is not None:
            attachment["content_id"] = _reject_control_chars(
                _require_str(content_id, "{} content_id".format(label)),
                "{} content_id".format(label),
            )
        built.append(attachment)
    return built, total


def _build_headers(value: Any) -> Dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValidationError("headers must be a mapping")
    if len(value) > MAX_HEADERS:
        raise ValidationError(
            "too many custom headers: {} (limit {})".format(len(value), MAX_HEADERS)
        )

    headers: Dict[str, str] = {}
    for name, header_value in value.items():
        name = _require_str(name, "header name")
        _reject_control_chars(name, 'header name "{}"'.format(name))
        if not name.strip():
            raise ValidationError("a header name must not be empty")
        if name.strip().lower() in FORBIDDEN_HEADERS:
            raise ValidationError('header "{}" cannot be overridden'.format(name))
        text = _require_str(header_value, 'header "{}"'.format(name))
        _reject_control_chars(text, 'header "{}"'.format(name))
        headers[name] = text
    return headers


def _build_tags(value: Any) -> Dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValidationError("tags must be a mapping")
    if len(value) > MAX_TAGS:
        raise ValidationError("too many tags: {} (limit {})".format(len(value), MAX_TAGS))

    tags: Dict[str, str] = {}
    for key, tag_value in value.items():
        key = _require_str(key, "tag name")
        text = _require_str(tag_value, 'tag "{}"'.format(key))
        # The server truncates over-long tags. Rejecting instead means a caller
        # never wonders why their analytics group by a key that lost its tail.
        # Counted in UTF-16 units because the server truncates by JavaScript's
        # `.length`: an emoji is 2 there, and counting it as 1 here would let
        # through a tag the server then silently shortens.
        if _utf16_len(key) > MAX_TAG_KEY_LENGTH:
            raise ValidationError(
                'tag name "{}" is longer than {} characters'.format(key, MAX_TAG_KEY_LENGTH)
            )
        if _utf16_len(text) > MAX_TAG_VALUE_LENGTH:
            raise ValidationError(
                'tag "{}" value is longer than {} characters'.format(key, MAX_TAG_VALUE_LENGTH)
            )
        tags[key] = text
    return tags


#: Every spelling send() understands. Anything else is a typo, and a typo here
#: is silent data loss: `htlm=` would send a message with no body at all.
_SEND_PARAMS = frozenset(
    {
        "from", "from_", "to", "cc", "bcc", "reply_to", "replyTo", "subject",
        "html", "text", "headers", "attachments", "tags", "idempotency_key",
    }
)


def _reject_unknown_params(params: Mapping[str, Any]) -> None:
    unknown = sorted(str(name) for name in params if name not in _SEND_PARAMS)
    if not unknown:
        return
    hints = []
    for name in unknown:
        close = difflib.get_close_matches(name, sorted(_SEND_PARAMS), n=1)
        hint = ' (did you mean "{}"?)'.format(close[0]) if close else ""
        hints.append('"{}"{}'.format(name, hint))
    raise ValidationError("unknown send() parameter: {}".format(", ".join(hints)))


def _utf16_len(value: str) -> int:
    """Length the way the server measures it: JavaScript's `.length`."""
    return len(value.encode("utf-16-le", "surrogatepass")) // 2


def _pick(params: Mapping[str, Any], *names: str) -> Any:
    """First present spelling wins; a value of None counts as absent."""
    for name in names:
        if params.get(name) is not None:
            return params[name]
    return None


class Emails:
    """`client.emails`."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    def send(
        self,
        params: Optional[SendParams] = None,
        /,
        **kwargs: Any,
    ) -> SendEmailResponse:
        """Send one message.

        Two calling styles, on purpose:

            nm.emails.send(from_="Acme <hello@acme.com>", to="c@example.com", ...)
            nm.emails.send({"from": "Acme <hello@acme.com>", "to": "c@example.com", ...})

        The dict form is what every other transactional-email SDK takes, so a
        port from one of them is a rename of the import and nothing else. The
        keyword form spells `from` as `from_`, since `from` is reserved.

        Returns once the API has accepted the message (HTTP 202) — accepted
        means queued, not delivered.
        """
        merged: Dict[str, Any] = {}
        if params is not None:
            if not isinstance(params, Mapping):
                raise ValidationError(
                    "send() takes a mapping of parameters or keyword arguments, got {}".format(
                        type(params).__name__
                    )
                )
            merged.update(params)
        merged.update(kwargs)

        _reject_unknown_params(merged)
        try:
            body, idempotency_key, message_bytes = self._build_send_body(merged)
        except UnicodeEncodeError:
            # A lone surrogate (half an emoji, from a bad slice or a bad decode)
            # cannot be written as UTF-8; say so rather than leak a codec error.
            raise ValidationError(
                "a field contains text that is not valid UTF-8 (a lone surrogate)"
            ) from None
        # Measured the way the server measures it (SDK-CONTRACT.md section 5.7):
        # html + text as UTF-8 plus the *decoded* attachment bytes. Measuring the
        # encoded JSON refused 7.5–10 MiB attachments the server would take.
        if message_bytes > MAX_BYTES:
            raise ValidationError(
                "message is {} bytes (html + text + attachments), over the {} byte "
                "limit".format(message_bytes, MAX_BYTES)
            )
        try:
            payload = encode_json(body)
        except UnicodeEncodeError:
            raise ValidationError(
                "a field contains text that is not valid UTF-8 (a lone surrogate)"
            ) from None

        response = self._transport.request(
            "POST",
            "/v1/emails",
            payload=payload,
            # Header only (SDK-CONTRACT.md section 2): the server reads the header
            # first, so a body copy is redundant and two copies can only
            # disagree. http.client writes header values as Latin-1, so the
            # UTF-8 bytes are smuggled through as their Latin-1 spelling — the
            # wire then carries exactly the key's UTF-8 bytes.
            extra_headers={
                "Idempotency-Key": idempotency_key.encode("utf-8").decode("latin-1")
            },
        )
        data = response.data
        if (
            not isinstance(data, Mapping)
            or not isinstance(data.get("id"), str)
            or not data["id"]
        ):
            # Raised after the transport returned, so it is never retried: the
            # message may well have been accepted.
            raise ServerError(
                "malformed response: the send response did not contain a message id",
                status_code=response.status,
                request_id=response.request_id,
                body=data,
                parsed_body=data,
                retryable=False,
            )
        return SendEmailResponse.from_dict(data)

    def get(self, email_id: str) -> Email:
        """Retrieve one message by id, scoped to the team the key belongs to."""
        email_id = _require_str(email_id, "email id")
        if not email_id.strip():
            raise ValidationError("an email id is required")
        _reject_control_chars(email_id, "email id")

        # Percent-encode with nothing safe: an id carrying `/` or `?` would
        # otherwise rewrite the request path, and the id comes from whatever
        # store the caller kept it in.
        path = "/v1/emails/" + urllib.parse.quote(email_id, safe="")
        response = self._transport.request("GET", path)
        data = response.data
        if not isinstance(data, Mapping) or not data.get("id"):
            raise ServerError(
                "retrieve response did not contain a message id",
                status_code=response.status,
                request_id=response.request_id,
                body=data,
            )
        return Email.from_dict(data)

    def _build_send_body(
        self, params: Mapping[str, Any]
    ) -> Tuple[Dict[str, Any], str, int]:
        sender = _pick(params, "from_", "from")
        if sender is None:
            raise ValidationError('"from" is required (pass from_= or {"from": ...})')
        if params.get("from_") is not None and params.get("from") is not None:
            if params["from_"] != params["from"]:
                raise ValidationError(
                    'both "from_" and "from" were given with different values'
                )
        sender = _require_str(sender, '"from"')
        _reject_control_chars(sender, '"from"')
        if not sender.strip():
            raise ValidationError('"from" must not be empty')

        to = _normalise_recipients(params.get("to"), "to")
        if not to:
            raise ValidationError('"to" is required')
        cc = _normalise_recipients(params.get("cc"), "cc")
        bcc = _normalise_recipients(params.get("bcc"), "bcc")
        reply_to = _normalise_recipients(_pick(params, "reply_to", "replyTo"), "reply_to")

        total = len(to) + len(cc) + len(bcc)
        if total > MAX_RECIPIENTS:
            raise ValidationError(
                "too many recipients: {} across to, cc and bcc (limit {})".format(
                    total, MAX_RECIPIENTS
                )
            )

        # Always sent, even empty: the server defaults it to "" anyway, and a
        # body whose shape does not depend on the caller's arguments is one
        # fewer thing to reproduce when a message goes out wrong.
        subject = _reject_control_chars(
            _require_str(params.get("subject", ""), '"subject"'), '"subject"'
        )

        body: Dict[str, Any] = {"from": sender, "to": to, "subject": subject}
        if cc:
            body["cc"] = cc
        if bcc:
            body["bcc"] = bcc
        if reply_to:
            body["reply_to"] = reply_to

        message_bytes = 0
        html = params.get("html")
        if html is not None:
            body["html"] = _require_str(html, '"html"')
            message_bytes += len(body["html"].encode("utf-8"))
        text = params.get("text")
        if text is not None:
            body["text"] = _require_str(text, '"text"')
            message_bytes += len(body["text"].encode("utf-8"))

        headers = _build_headers(params.get("headers"))
        if headers:
            body["headers"] = headers
        attachments, attachment_bytes = _build_attachments(params.get("attachments"))
        message_bytes += attachment_bytes
        if attachments:
            body["attachments"] = attachments
        tags = _build_tags(params.get("tags"))
        if tags:
            body["tags"] = tags

        supplied = params.get("idempotency_key")
        if supplied is not None:
            _require_str(supplied, "idempotency_key")
        if supplied is not None and supplied.strip():
            idempotency_key = supplied
            # It becomes an HTTP header, so a newline in it is an injection into
            # our own request, not just the message.
            _reject_control_chars(idempotency_key, "idempotency_key")
            size = len(idempotency_key.encode("utf-8"))
            if size > MAX_IDEMPOTENCY_KEY_BYTES:
                raise ValidationError(
                    "idempotency_key is {} bytes of UTF-8, over the {}-byte limit".format(
                        size, MAX_IDEMPOTENCY_KEY_BYTES
                    )
                )
        else:
            # An empty key counts as none supplied (SDK-CONTRACT.md section 5.4).
            # Generated once per send() call and reused across every retry of
            # that call. Without it the retry policy in SDK-CONTRACT.md section 4
            # would double-mail a customer every time a response was lost in
            # transit — which is the common failure, not the exotic one.
            idempotency_key = str(uuid.uuid4())

        return body, idempotency_key, message_bytes

    def __repr__(self) -> str:
        return "<nc_email.Emails>"
