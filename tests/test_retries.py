"""Retry policy and automatic idempotency — SDK-CONTRACT.md sections 4 and 5.4.

These two are one feature. Retrying a POST is only safe because every attempt
carries the same `Idempotency-Key`; without that, the timeout-then-retry case —
which is the common failure, not the exotic one — would send a customer two
copies of the same receipt.

Some assertions here are wall-clock. That is deliberate: honouring `Retry-After`
is the behaviour, and a test that reached into the sleep call to check it would
be asserting on an implementation detail rather than on what the server asked
for.
"""

from __future__ import annotations

import email.utils
import time
import unittest

from _support import TEST_KEY, MockAPI

from nc_email import (
    AuthenticationError,
    ConflictError,
    Naijamail,
    NaijamailPermissionError,
    NotFoundError,
    RateLimitError,
    ServerError,
    ValidationError,
)

MINIMAL = {"from_": "a@acme.com", "to": "x@y.com"}


class RetryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.api = MockAPI().start()
        self.addCleanup(self.api.stop)

    def client(self, **kwargs) -> Naijamail:  # type: ignore[no-untyped-def]
        kwargs.setdefault("max_retries", 2)
        return Naijamail(TEST_KEY, base_url=self.api.base_url, **kwargs)


class RetryTest(RetryTestCase):
    def test_retries_a_500_then_succeeds(self) -> None:
        self.api.enqueue_error(500, "Internal server error")
        self.api.enqueue_accepted(message_id="msg-2")
        sent = self.client().emails.send(**MINIMAL)
        self.assertEqual(sent.id, "msg-2")
        self.assertEqual(len(self.api.requests), 2)

    def test_three_attempts_by_default_then_raises(self) -> None:
        for _ in range(3):
            self.api.enqueue_error(503, "Service unavailable")
        with self.assertRaises(ServerError):
            self.client().emails.send(**MINIMAL)
        self.assertEqual(len(self.api.requests), 3)

    def test_max_retries_zero_means_one_attempt(self) -> None:
        self.api.enqueue_error(500, "boom")
        with self.assertRaises(ServerError):
            self.client(max_retries=0).emails.send(**MINIMAL)
        self.assertEqual(len(self.api.requests), 1)

    def test_retries_a_408(self) -> None:
        self.api.enqueue_error(408, "Request Timeout")
        self.api.enqueue_accepted()
        self.client().emails.send(**MINIMAL)
        self.assertEqual(len(self.api.requests), 2)

    def test_get_is_retried_too(self) -> None:
        self.api.enqueue_error(500, "boom")
        self.api.enqueue_json(
            200,
            {
                "id": "1",
                "to": "x@y.com",
                "from": "a@acme.com",
                "subject": "",
                "status": "sent",
                "created_at": "2026-08-29T10:00:00.000Z",
                "opened": False,
                "clicked": False,
            },
        )
        self.assertEqual(self.client().emails.get("1").id, "1")
        self.assertEqual(len(self.api.requests), 2)

    def test_never_retries_a_non_retryable_4xx(self) -> None:
        cases = [
            (400, ValidationError),
            (401, AuthenticationError),
            (403, NaijamailPermissionError),
            (404, NotFoundError),
            (409, ConflictError),
            (422, ValidationError),
        ]
        for status, expected in cases:
            with self.subTest(status=status):
                api = MockAPI().start()
                self.addCleanup(api.stop)
                api.enqueue_error(status, "no")
                client = Naijamail(TEST_KEY, base_url=api.base_url, max_retries=2)
                with self.assertRaises(expected):
                    client.emails.send(**MINIMAL)
                # A 403 on an unverified domain will never succeed; retrying it
                # only makes the caller wait longer for the same answer.
                self.assertEqual(len(api.requests), 1)

    def test_never_follows_a_redirect_and_never_retries_it(self) -> None:
        self.api.enqueue_raw(302, b"", {"Location": "https://evil.example.com/v1/emails"})
        with self.assertRaises(ServerError) as caught:
            self.client().emails.send(**MINIMAL)
        self.assertEqual(caught.exception.message, "unexpected redirect")
        self.assertEqual(len(self.api.requests), 1)


class RetryAfterTest(RetryTestCase):
    def test_integer_seconds_are_honoured(self) -> None:
        self.api.enqueue_error(429, "Too many requests", headers={"Retry-After": "1"})
        self.api.enqueue_accepted()
        started = time.monotonic()
        self.client().emails.send(**MINIMAL)
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 0.9)
        self.assertLess(elapsed, 5.0)
        self.assertEqual(len(self.api.requests), 2)

    def test_an_http_date_is_honoured(self) -> None:
        # RFC 9110 allows both spellings and intermediaries emit both; handling
        # only the integer form means silently ignoring a proxy's throttle.
        when = email.utils.formatdate(time.time() + 2, usegmt=True)
        self.api.enqueue_error(429, "Too many requests", headers={"Retry-After": when})
        self.api.enqueue_accepted()
        started = time.monotonic()
        self.client().emails.send(**MINIMAL)
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 0.9)
        self.assertLess(elapsed, 6.0)

    def test_a_malformed_retry_after_falls_back_to_backoff(self) -> None:
        self.api.enqueue_error(429, "Too many requests", headers={"Retry-After": "soon"})
        self.api.enqueue_accepted()
        self.client().emails.send(**MINIMAL)
        self.assertEqual(len(self.api.requests), 2)

    def test_retry_after_is_clamped(self) -> None:
        self.api.enqueue_error(429, "Too many requests", headers={"Retry-After": "86400"})
        with self.assertRaises(RateLimitError) as caught:
            self.client(max_retries=0).emails.send(**MINIMAL)
        # A day-long header must never become a day-long sleep on a caller's
        # request thread.
        self.assertEqual(caught.exception.retry_after, 60.0)


class IdempotencyTest(RetryTestCase):
    def test_one_generated_key_across_every_attempt(self) -> None:
        for _ in range(2):
            self.api.enqueue_error(500, "boom")
        self.api.enqueue_accepted()
        self.client().emails.send(**MINIMAL)

        self.assertEqual(len(self.api.requests), 3)
        keys = {request.headers["idempotency-key"] for request in self.api.requests}
        self.assertEqual(len(keys), 1, "a retry with a fresh key would send a second email")

    def test_two_calls_get_different_keys(self) -> None:
        self.api.enqueue_accepted()
        self.api.enqueue_accepted()
        client = self.client()
        client.emails.send(**MINIMAL)
        client.emails.send(**MINIMAL)
        first, second = (request.headers["idempotency-key"] for request in self.api.requests)
        self.assertNotEqual(first, second)

    def test_the_generated_key_is_also_in_the_body(self) -> None:
        # Belt and braces against an intermediary that strips unknown headers:
        # both carry the same value, so which one the server prefers is moot.
        self.api.enqueue_accepted()
        self.client().emails.send(**MINIMAL)
        request = self.api.requests[-1]
        self.assertEqual(request.json["idempotency_key"], request.headers["idempotency-key"])

    def test_a_caller_supplied_key_wins_and_is_never_regenerated(self) -> None:
        self.api.enqueue_error(500, "boom")
        self.api.enqueue_accepted()
        self.client().emails.send(idempotency_key="order-1024", **MINIMAL)
        for request in self.api.requests:
            self.assertEqual(request.headers["idempotency-key"], "order-1024")
            self.assertEqual(request.json["idempotency_key"], "order-1024")


if __name__ == "__main__":
    unittest.main()
