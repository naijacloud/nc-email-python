"""Webhook signature verification (SDK-CONTRACT.md section 6).

The control plane delivers event webhooks to per-team endpoints, signed as
below. During a secret rotation the header carries two v1= values.

Header:  NC-Signature: t=1756468800,v1=<hex sha256 hmac>
Signed:  "<t>.<raw request body bytes>", HMAC-SHA256, hex (compared case-insensitively).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import time
from typing import Any, List, Mapping, Optional, Tuple, Union

from .errors import ValidationError, WebhookVerificationError
from .types import WebhookEvent

#: Five minutes. Long enough to survive a slow queue or a clock a little out of
#: step, short enough that a captured request is not a replay a day later.
DEFAULT_TOLERANCE_SECONDS = 300

_TIMESTAMP_RE = re.compile(r"[0-9]{1,12}")


def _parse_signature_header(header: str) -> Tuple[int, List[str]]:
    timestamp: Optional[int] = None
    signatures: List[str] = []
    for part in header.split(","):
        name, _, value = part.strip().partition("=")
        if not _:
            continue
        name = name.strip()
        value = value.strip()
        if name == "t" and timestamp is None:
            # 1-12 ASCII digits and nothing else (SDK-CONTRACT.md section 6).
            # int() alone accepts "+1", "1_000", " 1" and Arabic-Indic digits,
            # and an unbounded one overflows float arithmetic below.
            if not _TIMESTAMP_RE.fullmatch(value):
                raise WebhookVerificationError(
                    "signature header has a malformed timestamp"
                )
            timestamp = int(value)
        elif name == "v1":
            # Several are legal and expected: during a secret rotation the
            # sender signs with both the old and the new secret so neither end
            # has to cut over at an exact instant.
            signatures.append(value.lower())

    if timestamp is None:
        raise WebhookVerificationError("signature header is missing its timestamp")
    if not signatures:
        raise WebhookVerificationError("signature header contains no v1 signature")
    return timestamp, signatures


def _as_bytes(payload: Any) -> bytes:
    """The signature covers the bytes that arrived, never a re-serialised object.

    A dict round-tripped through `json.dumps` differs from the body the sender
    signed by key order, separator spacing and unicode escaping — so accepting
    one here would produce verification that fails at random, or worse, a
    caller who "fixes" it by turning the check off.
    """
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, (bytearray, memoryview)):
        return bytes(payload)
    if isinstance(payload, str):
        return payload.encode("utf-8")
    raise ValidationError(
        "payload must be the raw request body as bytes or str, got {}. A parsed "
        "object cannot be re-serialised to the exact bytes that were signed.".format(
            type(payload).__name__
        )
    )


class Webhooks:
    """Namespace for the verifier. No client and no API key involved: this runs
    inside the caller's own HTTP handler, against the endpoint secret."""

    @staticmethod
    def verify(
        payload: Union[bytes, bytearray, memoryview, str],
        signature_header: Optional[str],
        secret: Union[str, bytes],
        tolerance: int = DEFAULT_TOLERANCE_SECONDS,
    ) -> WebhookEvent:
        """Verify a signed webhook and return the decoded event.

        Raises `WebhookVerificationError` on anything that does not verify. The
        error never contains the expected signature: telling an attacker the
        value they failed to guess turns a rejected forgery into a working one.
        """
        body = _as_bytes(payload)

        # NaN compares false against everything, so `drift > NaN` would never
        # fire and every replayed event would pass. A tolerance read with
        # float(os.environ.get(...)) is exactly how one arrives.
        if (
            isinstance(tolerance, bool)
            or not isinstance(tolerance, (int, float))
            or not math.isfinite(tolerance)
            or tolerance < 0
        ):
            raise ValidationError("tolerance must be a finite number of seconds, 0 or more")

        if not signature_header or not isinstance(signature_header, str):
            raise WebhookVerificationError("missing NC-Signature header")

        if isinstance(secret, str):
            if secret.startswith("nmail_live_") or secret.startswith("nmail_test_"):
                # An easy mix-up, and one that would otherwise present as every
                # webhook failing to verify with no clue why.
                raise ValidationError(
                    "that is an API key, not a webhook signing secret (nmail_whsec_…)"
                )
            secret_bytes = secret.encode("utf-8")
        elif isinstance(secret, (bytes, bytearray)):
            secret_bytes = bytes(secret)
        else:
            raise ValidationError("secret must be a string or bytes")
        if not secret_bytes:
            raise ValidationError("a webhook signing secret is required")

        timestamp, signatures = _parse_signature_header(signature_header)

        # Whole seconds, as the sender stamps them: with a fractional `now`,
        # a tolerance of 0 would refuse even a signature from this second.
        drift = abs(int(time.time()) - timestamp)
        if drift > tolerance:
            # Checked before the HMAC so a replayed-but-genuine request is
            # rejected on its age rather than accepted on its signature. This is
            # the whole point of signing the timestamp alongside the body.
            raise WebhookVerificationError(
                "timestamp is {}s outside the {}s tolerance".format(int(drift), tolerance)
            )

        signed = str(timestamp).encode("ascii") + b"." + body
        expected = hmac.new(secret_bytes, signed, hashlib.sha256).hexdigest()

        matched = False
        for candidate in signatures:
            try:
                supplied = candidate.encode("ascii")
            except UnicodeEncodeError:
                continue
            # compare_digest, never ==: a byte-at-a-time comparison leaks how
            # much of a guess was right through its own timing, which is enough
            # to forge a signature one character at a time.
            if hmac.compare_digest(expected.encode("ascii"), supplied):
                matched = True
        if not matched:
            raise WebhookVerificationError("signature does not match")

        try:
            data = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise WebhookVerificationError("payload is not valid JSON") from None
        if not isinstance(data, Mapping):
            raise WebhookVerificationError("payload is not a JSON object")

        return WebhookEvent.from_dict(data)


#: Module-level alias, so a caller can `from nc_email import verify_webhook`.
verify_webhook = Webhooks.verify
