"""The client: credential handling, base-URL policy, and the two rules that keep
a live sending key out of places it must never reach.

Everything here that looks paranoid is one of the non-negotiables in
SDK-CONTRACT.md section 5. A leaked Naijamail key is not an information
disclosure — it is an attacker able to send mail as a customer's verified,
DKIM-signed domain, which is a phishing incident with our DNS vouching for it.
"""

from __future__ import annotations

import os
import platform
import re
import ssl
import urllib.parse
from typing import Any, Optional

from ._version import __version__
from .emails import Emails
from .errors import ValidationError
from .http import Transport

DEFAULT_BASE_URL = "https://api.naijacloud.com"
API_KEY_ENV_VAR = "NAIJAMAIL_API_KEY"
BASE_URL_ENV_VAR = "NAIJAMAIL_BASE_URL"

#: Two families are accepted, because the API accepts two:
#:
#:   ``nmail_live_`` / ``nmail_test_`` — a Naijamail-only key from the dashboard's
#:       Email screen. The test variant is refused by the send path, which is the
#:       point of it.
#:   ``nc_live_`` — a workspace API key carrying the Email send scope, from
#:       Settings -> API keys. One credential for mail, deploys and the platform
#:       API, so a customer who already has one does not need a second.
#:
#: Checked at construction so an empty string or a pasted-with-whitespace key
#: fails here, at import time in a deploy, rather than as a 401 the first time a
#: customer triggers a receipt. Kept as an allowlist rather than relaxed to "any
#: non-empty string": the check exists to catch the truncated paste and the
#: wrong-variable-name deploy, and a pattern that accepts anything catches
#: neither.
API_KEY_PATTERN = re.compile(r"^(?:nmail_(?:live|test)|nc_live)_[A-Za-z0-9_-]{8,}$")

#: The prefixes above, for redaction. Order matters only for readability; they
#: cannot both match the same string.
API_KEY_PREFIXES = ("nmail_live_", "nmail_test_", "nc_live_")

#: The only hosts allowed to be reached over plaintext. A dev control plane on a
#: loopback address never leaves the machine; anything else would put a live
#: credential on the wire in clear.
PLAINTEXT_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def redact_key(api_key: str) -> str:
    """`nmail_live_***`.

    The prefix is kept because it is the useful half — it tells an operator
    reading a log whether the process is holding a live or a test key. The rest
    is never shown, not even the last four: with a fixed prefix and a known
    length, every character given away is search space an attacker does not have
    to cover.
    """
    for prefix in API_KEY_PREFIXES:
        if api_key.startswith(prefix):
            return prefix + "***"
    return "***"


def _default_user_agent(suffix: Optional[str] = None) -> str:
    agent = "nc-email-python/{} (python/{})".format(__version__, platform.python_version())
    if suffix:
        agent = "{} {}".format(agent, suffix)
    return agent


def _validate_base_url(base_url: str) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValidationError(
            "base_url must be an absolute URL such as https://api.naijacloud.com, got {!r}".format(
                base_url
            )
        )
    if parsed.scheme == "http":
        host = (parsed.hostname or "").lower()
        if host not in PLAINTEXT_HOSTS:
            raise ValidationError(
                "base_url must use https (got {!r}). Plaintext is allowed only for "
                "localhost, 127.0.0.1 and ::1, where the request never leaves the "
                "machine.".format(base_url)
            )
    elif parsed.scheme != "https":
        raise ValidationError(
            "base_url must use https, got scheme {!r}".format(parsed.scheme)
        )
    # Trailing slashes are the classic source of `//v1/emails`.
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


class Naijamail:
    """A Naijamail API client.

        nm = Naijamail()                       # reads NAIJAMAIL_API_KEY
        sent = nm.emails.send(from_="Acme <hello@acme.com>", to="c@example.com",
                              subject="Hi", html="<p>Hi</p>")
        email = nm.emails.get(sent.id)

    One client owns one key, one base URL and one HTTP opener. Nothing is held
    at module level, so two clients with two keys in one process cannot reach
    each other's state — which is what makes a per-tenant client safe.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
        max_retries: int = 2,
        user_agent_suffix: Optional[str] = None,
        ssl_context: Optional[ssl.SSLContext] = None,
    ) -> None:
        key = api_key if api_key is not None else os.environ.get(API_KEY_ENV_VAR)
        if not key:
            raise ValidationError(
                "no API key: pass api_key= or set the {} environment variable".format(
                    API_KEY_ENV_VAR
                )
            )
        if not isinstance(key, str):
            raise ValidationError("api_key must be a string")
        if not API_KEY_PATTERN.match(key):
            # The key itself is never echoed, here least of all: a construction
            # error is exactly the kind of thing that ends up in a CI log.
            raise ValidationError(
                "api_key does not look like a Naijamail key (expected nmail_live_…, "
                "nmail_test_… or nc_live_…)"
            )

        resolved_base = base_url or os.environ.get(BASE_URL_ENV_VAR) or DEFAULT_BASE_URL
        resolved_base = _validate_base_url(_require_text(resolved_base, "base_url"))

        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ValidationError("timeout must be a positive number of seconds")
        if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 0:
            raise ValidationError("max_retries must be a non-negative integer")

        if user_agent_suffix is not None:
            user_agent_suffix = _require_text(user_agent_suffix, "user_agent_suffix")
            for char in ("\r", "\n", "\0"):
                if char in user_agent_suffix:
                    raise ValidationError(
                        "user_agent_suffix must not contain carriage returns, newlines "
                        "or NUL bytes"
                    )
            if key in user_agent_suffix:
                # Someone building a suffix out of an f-string of their config.
                # The User-Agent is logged by every proxy between here and us.
                raise ValidationError("user_agent_suffix must not contain the API key")

        self._key_display = redact_key(key)
        self.timeout = timeout
        self.max_retries = max_retries
        # The key lives in the transport and nowhere else on this object, so
        # `vars(client)` — which is what a debugger, a `pprint` and most
        # log-the-object helpers reach for — cannot surface it.
        self._transport = Transport(
            api_key=key,
            base_url=resolved_base,
            timeout=timeout,
            max_retries=max_retries,
            user_agent=_default_user_agent(user_agent_suffix),
            ssl_context=ssl_context,
        )
        self.emails = Emails(self._transport)

    @property
    def base_url(self) -> str:
        return self._transport.base_url

    @property
    def user_agent(self) -> str:
        return self._transport.user_agent

    @property
    def masked_api_key(self) -> str:
        """The only spelling of the key this object will ever hand back."""
        return self._key_display

    def __repr__(self) -> str:
        return "<Naijamail base_url={!r} api_key={!r}>".format(
            self.base_url, self._key_display
        )

    __str__ = __repr__

    def __getstate__(self) -> Any:
        # Pickling a client would write the key into whatever the caller is
        # serialising into — a cache, a Celery task payload, a crash dump — and
        # none of those are places a sending credential should come to rest.
        raise TypeError("a Naijamail client holds an API key and cannot be serialised")


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValidationError("{} must be a string, got {}".format(label, type(value).__name__))
    return value
