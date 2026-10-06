"""The error taxonomy, fixed by SDK-CONTRACT.md section 3.

Every failure — a 4xx from the API, a socket that never connected, or a caller
mistake caught before the request leaves this process — is one class hanging off
`NaijamailError`. A caller who wants one `except` writes one; a caller who needs
to tell "unverified domain" from "rate limited" gets separate types instead of
having to read `status_code`.

Errors raised locally carry `status_code = 0`. That is deliberate: it is how a
caller (or a log line) tells "we never asked the server" from "the server said
no", and it means the same `except NaijamailError` covers both.

The key never appears in any of these fields. Exception objects end up in logs,
Sentry and bug reports, which is exactly where a sending credential must not be.
"""

from __future__ import annotations

from typing import Any, List, Optional, Union


def _join_message(message: Union[str, List[Any], Any]) -> str:
    """Flatten NestJS's `message`, which is a string or an array of strings.

    class-validator returns one string per failed constraint, so a validation
    failure arrives as an array. Joining rather than taking the first keeps the
    caller from fixing one field at a time across three round trips.
    """
    if isinstance(message, str):
        return message
    if isinstance(message, (list, tuple)):
        return "; ".join(str(part) for part in message)
    return str(message)


class NaijamailError(Exception):
    """Base class for everything this SDK raises.

    `retryable` is carried on the instance rather than derived from the class so
    a single class can answer both ways where the status alone is not enough —
    see the redirect case in `http.py`, which is a `ServerError` that must never
    be retried because retrying re-sends the credential.
    """

    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 0,
        error: Optional[str] = None,
        request_id: Optional[str] = None,
        body: Any = None,
        retryable: Optional[bool] = None,
        raw_body: Optional[str] = None,
        parsed_body: Any = None,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error = error
        self.request_id = request_id
        # The raw decoded body, so a caller can inspect a field this SDK version
        # does not know about instead of waiting for a release.
        self.body = body
        #: The response text exactly as received ("" for a local error or an
        #: empty body), and the parsed JSON body (None when it did not parse).
        #: Both always present, so a caller never guesses which `body` holds.
        self.raw_body = raw_body if raw_body is not None else ""
        self.parsed_body = parsed_body
        #: Seconds from a `Retry-After` header on a retryable response, parsed
        #: and clamped to 60. Honoured by the retry loop whatever the status.
        self.retry_after = retry_after
        if retryable is not None:
            self.retryable = retryable

    def __str__(self) -> str:
        return self.message

    def __repr__(self) -> str:
        return "{}(message={!r}, status_code={!r}, error={!r}, request_id={!r})".format(
            type(self).__name__, self.message, self.status_code, self.error, self.request_id
        )


class ValidationError(NaijamailError):
    """400, 413, 422 and any other unlisted 4xx, or a caller mistake caught here
    before any network call. Either way the request as sent will never succeed."""


class AuthenticationError(NaijamailError):
    """401. Missing, malformed, unknown or revoked key — the server will not say which."""


class NaijamailPermissionError(NaijamailError):
    """403. Authenticated but not allowed: a test key on the send path, an
    unverified From domain, a paused domain, or the daily quota.

    Named with the prefix because `PermissionError` is a builtin (an OSError).
    `nc_email.PermissionError` is an alias below for callers who prefer the
    short spelling; it is kept out of `__all__` so `import *` cannot shadow the
    builtin in someone else's module.
    """


class NotFoundError(NaijamailError):
    """404 — and the 400 the retrieve endpoint answers for an unknown id."""


class ConflictError(NaijamailError):
    """409."""


class RateLimitError(NaijamailError):
    """429. `retry_after` is seconds, already parsed from the header and clamped."""

    retryable = True


class ServerError(NaijamailError):
    """5xx, and any response this SDK cannot make sense of."""

    retryable = True


class NaijamailConnectionError(NaijamailError):
    """DNS, TCP or TLS failure — the request never got an answer.

    Prefixed for the same reason as the permission error: `ConnectionError` is a
    builtin. `nc_email.ConnectionError` aliases it.
    """

    retryable = True


class NaijamailTimeoutError(NaijamailError):
    """The per-attempt deadline expired, or the server answered 408.

    Retrying is safe only because `send()` pins one idempotency key across every
    attempt — see SDK-CONTRACT.md section 5.4.
    """

    retryable = True


class WebhookVerificationError(NaijamailError):
    """A webhook signature did not verify, or its timestamp is outside tolerance.

    The message never contains the expected signature: handing an attacker the
    value they failed to guess turns a rejected forgery into a working one.
    """


# Short spellings, for callers who would rather write `nc_email.TimeoutError`.
# Deliberately absent from `__all__` in `__init__.py`: a `from nc_email import *`
# that quietly replaced the builtin `ConnectionError` in the importing module
# would break unrelated `except` clauses in ways nobody would connect back here.
PermissionError = NaijamailPermissionError
ConnectionError = NaijamailConnectionError
TimeoutError = NaijamailTimeoutError
