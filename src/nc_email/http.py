"""The HTTP layer: one opener, the error mapping, and the retry loop.

`urllib.request` rather than `requests` or `httpx`. This package holds a live
sending credential, so every third-party runtime dependency is another
maintainer who could ship a post-install script into a process that can mail as
our customers' verified domains (SDK-CONTRACT.md section 5.9). The standard
library is the smaller attack surface, and the code it costs us is this file.
"""

from __future__ import annotations

import functools
import http.client
import io
import json
import random
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Mapping, Optional, Tuple

from .errors import (
    AuthenticationError,
    ConflictError,
    NaijamailConnectionError,
    NaijamailError,
    NaijamailPermissionError,
    NaijamailTimeoutError,
    NotFoundError,
    RateLimitError,
    ServerError,
    ValidationError,
    _join_message,
)

#: Full-jitter backoff, from SDK-CONTRACT.md section 4.
BACKOFF_BASE_SECONDS = 0.5
BACKOFF_CAP_SECONDS = 8.0
#: A server that asks for more than a minute is asking us to hold the caller's
#: thread hostage. Honour the header, but not unboundedly.
RETRY_AFTER_MAX_SECONDS = 60.0

#: The exact 400 body the retrieve endpoint returns for an id it cannot find.
#: The control plane raises BadRequestException('message not found') there
#: instead of a 404 (tracked in email-sdks/GAPS.md); matching the exact string —
#: not a substring — keeps a future genuine validation error that merely
#: mentions those words from being reported to callers as "not found".
MESSAGE_NOT_FOUND = "message not found"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse every 3xx instead of following it.

    urllib's default handler replays the original request — headers and all — at
    whatever host the `Location` names. That would put `Authorization: Bearer
    nmail_live_…` on a request to a machine we never chose to talk to, which is
    precisely how bearer tokens leak. Returning None makes the opener fall
    through to the default error handler, which raises HTTPError with the 3xx
    status; `_error_for_response` turns that into a ServerError.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


# -- the per-attempt deadline ---------------------------------------------------
#
# urllib's `timeout` is a per-socket-operation timeout: each connect, send and
# recv gets the full allowance afresh, so a server (or a broken middlebox) that
# trickles one byte every few seconds keeps a "30s" request alive indefinitely.
# SDK-CONTRACT.md section 4 makes `timeout` a deadline for the whole attempt.
# The pieces below carry a monotonic deadline on the Request into the
# connection, and re-arm the socket timeout to whatever is *left* before every
# send and every read — so the attempt ends at the deadline however the bytes
# are paced, without a watchdog thread per request.


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        # settimeout(0) would mean non-blocking, not "expired".
        raise socket.timeout("the per-attempt deadline expired")
    return left


class _DeadlineRaw(io.RawIOBase):
    """Reads from a socket file, re-arming the socket timeout to the time left."""

    def __init__(self, sock: Any, deadline: float) -> None:
        super().__init__()
        self._sock = sock
        # Unbuffered SocketIO: it takes an io reference on the socket, which is
        # what keeps the descriptor open after urllib closes the connection's
        # own handle right after reading the headers.
        self._io = sock.makefile("rb", buffering=0)
        self._deadline = deadline

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> Optional[int]:  # type: ignore[override]
        self._sock.settimeout(_remaining(self._deadline))
        return self._io.readinto(buffer)  # type: ignore[no-any-return]

    def close(self) -> None:
        if not self.closed:
            self._io.close()
        super().close()


class _DeadlineSocket:
    """Just enough of a socket for HTTPResponse, which only calls makefile()."""

    def __init__(self, sock: Any, deadline: float) -> None:
        self._sock = sock
        self._deadline = deadline

    def makefile(self, mode: str = "rb", *args: Any, **kwargs: Any) -> io.BufferedReader:
        return io.BufferedReader(_DeadlineRaw(self._sock, self._deadline))


class _DeadlineResponse(http.client.HTTPResponse):
    def __init__(self, sock: Any, *args: Any, nc_deadline: float, **kwargs: Any) -> None:
        super().__init__(_DeadlineSocket(sock, nc_deadline), *args, **kwargs)  # type: ignore[arg-type]


def _deadline_connection(base: Any) -> Any:
    class _Connection(base):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, nc_deadline: Optional[float] = None, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._nc_deadline = nc_deadline
            if nc_deadline is not None:
                self.response_class = functools.partial(
                    _DeadlineResponse, nc_deadline=nc_deadline
                )

        def connect(self) -> None:
            if self._nc_deadline is not None:
                # TCP connect and the TLS handshake run with what is left.
                self.timeout = _remaining(self._nc_deadline)
            super().connect()

        def send(self, data: Any) -> None:
            if self._nc_deadline is not None and self.sock is not None:
                # sendall's timeout bounds the whole call (Python 3.5+).
                self.sock.settimeout(_remaining(self._nc_deadline))
            super().send(data)

    _Connection.__name__ = "_Deadline" + base.__name__
    return _Connection


_DEADLINE_CONNECTIONS: Dict[Any, Any] = {
    http.client.HTTPConnection: _deadline_connection(http.client.HTTPConnection),
    http.client.HTTPSConnection: _deadline_connection(http.client.HTTPSConnection),
}


def _bind_deadline(http_class: Any, req: urllib.request.Request) -> Any:
    deadline = getattr(req, "_nc_deadline", None)
    wrapped = _DEADLINE_CONNECTIONS.get(http_class)
    if deadline is None or wrapped is None:
        return http_class
    return functools.partial(wrapped, nc_deadline=deadline)


class _DeadlineHTTPHandler(urllib.request.HTTPHandler):
    def do_open(self, http_class: Any, req: Any, **kwargs: Any) -> Any:
        return super().do_open(_bind_deadline(http_class, req), req, **kwargs)


class _DeadlineHTTPSHandler(urllib.request.HTTPSHandler):
    def do_open(self, http_class: Any, req: Any, **kwargs: Any) -> Any:
        return super().do_open(_bind_deadline(http_class, req), req, **kwargs)


def build_opener(ssl_context: Optional[ssl.SSLContext] = None) -> urllib.request.OpenerDirector:
    """Build an opener with certificate verification on and redirects off.

    The context is created explicitly rather than left to urllib's default so
    the SDK's TLS behaviour does not silently change with an ambient
    `ssl._create_default_https_context` monkeypatch — a common "fix" in
    corporate environments that turns verification off process-wide.
    """
    context = ssl_context or ssl.create_default_context()
    # verify_mode first: setting check_hostname on a CERT_NONE context raises,
    # which would turn "someone handed us a permissive context" into a crash
    # instead of the correction it should be.
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    return urllib.request.build_opener(
        _NoRedirectHandler, _DeadlineHTTPHandler, _DeadlineHTTPSHandler(context=context)
    )


def encode_json(payload: Any) -> bytes:
    """Serialise a request body once, so its size can be checked before sending."""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _decode_body(raw: bytes) -> Tuple[Any, str]:
    """Return (parsed JSON or None, the text we decoded).

    A proxy's HTML error page, a truncated body or an empty one must produce a
    normal SDK error rather than a JSONDecodeError from somewhere deep in a
    caller's stack trace.
    """
    text = raw.decode("utf-8", "replace") if raw else ""
    if not text.strip():
        return None, text
    try:
        return json.loads(text), text
    except ValueError:
        return None, text


def parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Parse `Retry-After`, which is integer seconds *or* an HTTP date.

    Both spellings are legal per RFC 9110 and intermediaries emit both, so
    handling only the integer form means silently ignoring a proxy's throttle
    and hammering it.
    """
    if not value:
        return None
    raw = value.strip()
    try:
        return max(0.0, min(RETRY_AFTER_MAX_SECONDS, float(int(raw))))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        # An HTTP date without a zone is UTC by definition; letting
        # `.timestamp()` assume local time would be wrong by hours.
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, min(RETRY_AFTER_MAX_SECONDS, when.timestamp() - time.time()))


def _error_for_response(
    status: int,
    headers: Mapping[str, str],
    raw: bytes,
    reason: str = "",
) -> NaijamailError:
    parsed, text = _decode_body(raw)
    body: Any = parsed if parsed is not None else (text or None)

    message = ""
    label: Optional[str] = None
    if isinstance(parsed, Mapping):
        if "message" in parsed:
            message = _join_message(parsed["message"])
        raw_error = parsed.get("error")
        label = raw_error if isinstance(raw_error, str) else None
    if not message:
        message = "HTTP {}{}".format(status, " " + reason if reason else "")

    request_id = headers.get("x-request-id")
    common: Dict[str, Any] = {
        "status_code": status,
        "error": label,
        "request_id": request_id,
        "body": body,
        "raw_body": text,
        "parsed_body": parsed,
    }
    if status in (408, 429) or status >= 500:
        # Honoured on any retryable response, not only a 429: a 503 from a
        # load balancer draining a node says when to come back just as clearly.
        common["retry_after"] = parse_retry_after(headers.get("retry-after"))

    if 300 <= status < 400:
        # Reported as a server error because the server did something we cannot
        # act on, and pinned non-retryable: the same redirect will come back
        # next time, and each retry is another chance to send the credential
        # somewhere it does not belong.
        return ServerError("unexpected redirect", retryable=False, **common)
    if status == 400:
        if message.strip().lower() == MESSAGE_NOT_FOUND:
            return NotFoundError(message, **common)
        return ValidationError(message, **common)
    if status == 401:
        return AuthenticationError(message, **common)
    if status == 403:
        return NaijamailPermissionError(message, **common)
    if status == 404:
        return NotFoundError(message, **common)
    if status == 408:
        return NaijamailTimeoutError(message, **common)
    if status == 409:
        return ConflictError(message, **common)
    if status in (413, 422):
        # 413: the body was over the server's size limit — the caller's input,
        # and no retry will shrink it.
        return ValidationError(message, **common)
    if status == 429:
        return RateLimitError(message, **common)
    if status >= 500:
        return ServerError(message, **common)
    if 400 <= status < 500:
        # Some other 4xx (405, 415, 451, whatever a proxy invents). The request
        # as sent will never succeed, which is what ValidationError tells a
        # caller — the same answer in all five SDKs (SDK-CONTRACT.md section 3).
        return ValidationError(message, **common)
    return NaijamailError(message, **common)


class Response:
    """A successful response, already decoded."""

    __slots__ = ("status", "headers", "data", "request_id")

    def __init__(self, status: int, headers: Mapping[str, str], data: Any) -> None:
        self.status = status
        self.headers = headers
        self.data = data
        self.request_id = headers.get("x-request-id")


class Transport:
    """Owns one opener, one key and one base URL.

    Instance state only — two clients with two keys in one process must not be
    able to interfere with each other (SDK-CONTRACT.md section 5.12), which
    rules out module-level caches of anything request-scoped.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout: float,
        max_retries: int,
        user_agent: str,
        ssl_context: Optional[ssl.SSLContext] = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout
        self._max_retries = max_retries
        self._user_agent = user_agent
        self._opener = build_opener(ssl_context)
        # Its own Random instance rather than the module-level one, so a
        # caller seeding `random` for reproducible tests cannot make every
        # client in the process back off in lockstep. Jitter is not a secret,
        # so a cryptographic generator would buy nothing.
        self._random = random.Random()  # noqa: S311

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def user_agent(self) -> str:
        return self._user_agent

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: Optional[bytes] = None,
        extra_headers: Optional[Mapping[str, str]] = None,
    ) -> Response:
        headers: Dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": self._user_agent,
            "Authorization": "Bearer " + self._api_key,
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if extra_headers:
            headers.update(extra_headers)

        url = self._base_url + path
        attempts = self._max_retries + 1
        for attempt in range(attempts):
            try:
                return self._attempt(method, url, payload, headers)
            except NaijamailError as exc:
                if not exc.retryable or attempt == attempts - 1:
                    raise
                self._wait(attempt, exc)
        # Unreachable: the loop either returns or raises on its last attempt.
        raise AssertionError("retry loop exited without a result")

    def _attempt(
        self, method: str, url: str, payload: Optional[bytes], headers: Mapping[str, str]
    ) -> Response:
        # S310 is about unvalidated schemes; this one is fixed at construction,
        # where a base_url that is not https (or http on loopback) is refused.
        request = urllib.request.Request(  # noqa: S310
            url, data=payload, headers=dict(headers), method=method
        )
        request._nc_deadline = time.monotonic() + self._timeout  # type: ignore[attr-defined]
        try:
            with self._opener.open(request, timeout=self._timeout) as raw_response:
                status = raw_response.getcode()
                response_headers = _lower_headers(raw_response.headers.items())
                body = raw_response.read()
        except urllib.error.HTTPError as exc:
            # HTTPError is itself a response: read it before it closes, or the
            # server's explanation is lost and the caller gets "HTTP 400".
            try:
                error_body = exc.read()
            except Exception:  # pragma: no cover - defensive
                error_body = b""
            finally:
                # HTTPError owns a socket; leaving it to the garbage collector
                # produces a ResourceWarning and holds the connection open.
                exc.close()
            # `from None`: everything HTTPError carried is now on the SDK
            # error, and the chained frames only push the server's message off
            # the top of the traceback.
            raise _error_for_response(
                exc.code, _lower_headers(exc.headers.items() if exc.headers else []),
                error_body, str(getattr(exc, "reason", "") or ""),
            ) from None
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise NaijamailTimeoutError(
                    "request timed out after {}s".format(self._timeout)
                ) from exc
            # Chained, unlike the HTTP errors above: for a DNS or TLS failure the
            # original exception is the only place the actual cause is written.
            raise NaijamailConnectionError(
                "could not reach {}: {}".format(url, reason)
            ) from exc
        except socket.timeout as exc:
            # A read timeout surfaces bare rather than wrapped in URLError.
            raise NaijamailTimeoutError(
                "request timed out after {}s".format(self._timeout)
            ) from exc
        except (http.client.HTTPException, OSError) as exc:
            raise NaijamailConnectionError("could not reach {}: {}".format(url, exc)) from exc

        parsed, text = _decode_body(body)
        if parsed is None and text.strip():
            # A 2xx that is not JSON is an intermediary talking, not the API.
            # Not retried: the request may well have been accepted, and the same
            # intermediary will answer the same way next time.
            raise ServerError(
                "malformed response: expected JSON, got {} bytes of {}".format(
                    len(body), response_headers.get("content-type", "unknown content type")
                ),
                status_code=status,
                request_id=response_headers.get("x-request-id"),
                body=text,
                raw_body=text,
                retryable=False,
            )
        return Response(status, response_headers, parsed)

    def _wait(self, attempt: int, exc: NaijamailError) -> None:
        retry_after = getattr(exc, "retry_after", None)
        if retry_after is not None:
            # The server knows its own capacity better than our backoff curve.
            delay = float(retry_after)
        else:
            ceiling = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2 ** attempt))
            # Full jitter, not "exponential plus a bit": the point is to break up
            # a thundering herd of clients that all failed at the same instant,
            # and only a delay drawn from the whole window does that.
            delay = self._random.uniform(0, ceiling)
        if delay > 0:
            time.sleep(delay)

    def __repr__(self) -> str:
        return "<nc_email.Transport base_url={!r}>".format(self._base_url)

    def __getstate__(self) -> Any:
        # Pickling would write the key to whatever the caller is serialising
        # into: a cache, a queue payload, a crash dump.
        raise TypeError("a Naijamail transport holds an API key and cannot be serialised")


def _lower_headers(items: Any) -> Dict[str, str]:
    """Header names are case-insensitive; lowercase once so lookups can be literal."""
    return {str(key).lower(): str(value) for key, value in items}
