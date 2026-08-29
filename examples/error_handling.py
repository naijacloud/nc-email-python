"""What each failure looks like, and which ones are worth reacting to.

    export NAIJAMAIL_API_KEY=nmail_live_...
    python examples/error_handling.py
"""

from __future__ import annotations

import os

from nc_email import (
    AuthenticationError,
    Naijamail,
    NaijamailConnectionError,
    NaijamailError,
    NaijamailPermissionError,
    NaijamailTimeoutError,
    NotFoundError,
    RateLimitError,
    ServerError,
    ValidationError,
)

FROM = os.environ.get("NAIJAMAIL_FROM", "Acme <hello@acme.com>")


def main() -> int:
    nm = Naijamail()

    try:
        sent = nm.emails.send(
            from_=FROM,
            to="customer@example.com",
            subject="Your receipt",
            html="<p>Thanks for your order.</p>",
        )
        print("queued:", sent.id)

    except ValidationError as exc:
        # status_code is 0 when the SDK caught it locally — a malformed address,
        # a CR in the subject, too many recipients. Fix the input; retrying the
        # same call will fail the same way.
        print("bad request:", exc.message, "(status", exc.status_code, ")")

    except AuthenticationError:
        # The server will not say whether the key is unknown, revoked or
        # malformed, so a probe cannot learn which. Check the deploy's env.
        print("the API key was not accepted")

    except NaijamailPermissionError as exc:
        # Usually an unverified From domain, or a test key on the live send
        # path. Neither gets better on a retry.
        print("not allowed:", exc.message)

    except RateLimitError as exc:
        # The SDK already retried and honoured Retry-After. Reaching here means
        # it was still limited on the last attempt.
        print("rate limited; the server asked for", exc.retry_after, "seconds")

    except (NaijamailTimeoutError, NaijamailConnectionError) as exc:
        # The message may or may not have been accepted. Re-sending is safe:
        # pass the same idempotency_key and a duplicate returns the original
        # message rather than mailing anyone twice.
        print("could not reach the API:", exc.message)

    except ServerError as exc:
        # Already retried up to max_retries. request_id is what support needs.
        print("server error:", exc.message, "request_id:", exc.request_id)

    except NaijamailError as exc:
        print("unexpected:", type(exc).__name__, exc.message)

    try:
        nm.emails.get("00000000-0000-0000-0000-000000000000")
    except NotFoundError:
        # The API answers 400 for an unknown id; the SDK maps it here so the
        # except clause reads the way you would expect.
        print("no such message")
    except NaijamailError as exc:
        print("lookup failed:", exc.message)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
