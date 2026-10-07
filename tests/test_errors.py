"""The status-to-exception mapping from SDK-CONTRACT.md section 3."""

from __future__ import annotations

import time
import unittest

from _support import TEST_KEY, CannedResponse, MockAPI, reserve_closed_port

from nc_email import (
    AuthenticationError,
    ConflictError,
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

MINIMAL = {"from_": "a@acme.com", "to": "x@y.com"}


class ErrorMappingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.api = MockAPI().start()
        self.addCleanup(self.api.stop)
        self.client = Naijamail(TEST_KEY, base_url=self.api.base_url, max_retries=0)

    def test_each_status_maps_to_its_type(self) -> None:
        cases = [
            (400, '"to" is required', ValidationError),
            (401, "invalid API key", AuthenticationError),
            (403, "not allowed to send from \"x@y.com\"", NaijamailPermissionError),
            (404, "Cannot POST /v1/emails", NotFoundError),
            (408, "Request Timeout", NaijamailTimeoutError),
            (409, "duplicate idempotency key", ConflictError),
            (422, "subject is too long", ValidationError),
            (429, "Too many requests", RateLimitError),
            (500, "Internal server error", ServerError),
            (502, "Bad gateway", ServerError),
            (503, "Service unavailable", ServerError),
        ]
        for status, message, expected in cases:
            with self.subTest(status=status):
                self.api.enqueue_error(status, message)
                with self.assertRaises(expected) as caught:
                    self.client.emails.send(**MINIMAL)
                error = caught.exception
                self.assertEqual(error.status_code, status)
                self.assertEqual(error.message, message)
                self.assertIsInstance(error, NaijamailError)

    def test_400_message_not_found_is_a_not_found_error(self) -> None:
        self.api.enqueue_error(400, "message not found", error="Bad Request")
        with self.assertRaises(NotFoundError):
            self.client.emails.send(**MINIMAL)

    def test_400_that_merely_mentions_not_found_stays_a_validation_error(self) -> None:
        # The quirk is one exact string, not a substring: a genuine validation
        # message that happens to contain those words is not a 404.
        self.api.enqueue_error(400, 'header "message not found" cannot be overridden')
        with self.assertRaises(ValidationError):
            self.client.emails.send(**MINIMAL)

    def test_an_unlisted_4xx_is_a_validation_error(self) -> None:
        # SDK-CONTRACT.md section 3: the same answer in all five SDKs.
        for status in (405, 415, 451):
            with self.subTest(status=status):
                self.api.enqueue_error(status, "nope")
                start = len(self.api.requests)
                with self.assertRaises(ValidationError) as caught:
                    self.client.emails.send(**MINIMAL)
                self.assertEqual(caught.exception.status_code, status)
                self.assertFalse(caught.exception.retryable)
                self.assertEqual(len(self.api.requests) - start, 1)

    def test_raw_and_parsed_bodies_are_both_exposed(self) -> None:
        self.api.enqueue_error(403, "verify the domain first", error="Forbidden")
        with self.assertRaises(NaijamailPermissionError) as caught:
            self.client.emails.send(**MINIMAL)
        self.assertEqual(caught.exception.parsed_body["statusCode"], 403)
        self.assertIn('"verify the domain first"', caught.exception.raw_body)

    def test_a_non_json_error_has_raw_body_and_no_parsed_body(self) -> None:
        self.api.enqueue_raw(400, b"<html>bad</html>", {"Content-Type": "text/html"})
        with self.assertRaises(ValidationError) as caught:
            self.client.emails.send(**MINIMAL)
        self.assertIsNone(caught.exception.parsed_body)
        self.assertEqual(caught.exception.raw_body, "<html>bad</html>")

    def test_a_local_error_has_an_empty_raw_body(self) -> None:
        with self.assertRaises(ValidationError) as caught:
            self.client.emails.send(to="x@y.com")
        self.assertEqual(caught.exception.raw_body, "")
        self.assertIsNone(caught.exception.parsed_body)

    def test_message_arrays_are_joined(self) -> None:
        self.api.enqueue_error(400, ['"to" is required', '"from" is required'])
        with self.assertRaises(ValidationError) as caught:
            self.client.emails.send(**MINIMAL)
        self.assertEqual(caught.exception.message, '"to" is required; "from" is required')

    def test_the_server_error_label_and_body_are_kept(self) -> None:
        self.api.enqueue_error(403, "verify the domain first", error="Forbidden")
        with self.assertRaises(NaijamailPermissionError) as caught:
            self.client.emails.send(**MINIMAL)
        self.assertEqual(caught.exception.error, "Forbidden")
        self.assertEqual(caught.exception.body["statusCode"], 403)

    def test_request_id_is_captured(self) -> None:
        self.api.enqueue_error(500, "boom", headers={"x-request-id": "req_123"})
        with self.assertRaises(ServerError) as caught:
            self.client.emails.send(**MINIMAL)
        self.assertEqual(caught.exception.request_id, "req_123")

    def test_a_non_json_error_body_does_not_crash(self) -> None:
        # A proxy's HTML error page, which is what a customer actually sees when
        # something between them and us falls over.
        self.api.enqueue_raw(
            502, b"<html><body>502 Bad Gateway</body></html>", {"Content-Type": "text/html"}
        )
        with self.assertRaises(ServerError) as caught:
            self.client.emails.send(**MINIMAL)
        self.assertEqual(caught.exception.status_code, 502)
        self.assertIn("502", caught.exception.message)

    def test_an_empty_error_body_does_not_crash(self) -> None:
        self.api.enqueue_raw(500, b"")
        with self.assertRaises(ServerError) as caught:
            self.client.emails.send(**MINIMAL)
        self.assertIn("500", caught.exception.message)

    def test_a_non_json_success_body_is_a_server_error(self) -> None:
        self.api.enqueue_raw(202, b"<html>hello</html>", {"Content-Type": "text/html"})
        with self.assertRaises(ServerError):
            self.client.emails.send(**MINIMAL)

    def test_a_success_without_an_id_is_a_server_error(self) -> None:
        self.api.enqueue_json(202, {"status": "queued"})
        with self.assertRaises(ServerError):
            self.client.emails.send(**MINIMAL)

    def test_rate_limit_carries_retry_after(self) -> None:
        self.api.enqueue_error(429, "Too many requests", headers={"Retry-After": "7"})
        with self.assertRaises(RateLimitError) as caught:
            self.client.emails.send(**MINIMAL)
        self.assertEqual(caught.exception.retry_after, 7.0)

    def test_local_errors_have_status_code_zero(self) -> None:
        with self.assertRaises(ValidationError) as caught:
            self.client.emails.send(to="x@y.com")
        self.assertEqual(caught.exception.status_code, 0)

    def test_error_repr_is_useful_and_quiet(self) -> None:
        self.api.enqueue_error(403, "verify the domain first", error="Forbidden")
        with self.assertRaises(NaijamailPermissionError) as caught:
            self.client.emails.send(**MINIMAL)
        text = repr(caught.exception)
        self.assertIn("NaijamailPermissionError", text)
        self.assertIn("403", text)


class ConnectionFailureTest(unittest.TestCase):
    def test_connection_refused(self) -> None:
        port = reserve_closed_port()
        client = Naijamail(
            TEST_KEY, base_url="http://127.0.0.1:{}".format(port), max_retries=0, timeout=2.0
        )
        with self.assertRaises(NaijamailConnectionError) as caught:
            client.emails.send(**MINIMAL)
        self.assertEqual(caught.exception.status_code, 0)
        self.assertTrue(caught.exception.retryable)


class TimeoutTest(unittest.TestCase):
    def test_client_side_deadline(self) -> None:
        api = MockAPI().start()
        self.addCleanup(api.stop)
        api.enqueue_accepted(delay=1.0)
        client = Naijamail(TEST_KEY, base_url=api.base_url, max_retries=0, timeout=0.2)
        with self.assertRaises(NaijamailTimeoutError):
            client.emails.send(**MINIMAL)

    def test_the_deadline_covers_the_whole_response_not_each_read(self) -> None:
        # One byte every 0.1s never trips a 0.5s per-read timeout, but the body
        # takes ~3s in total. The attempt must end near the 0.5s deadline.
        api = MockAPI().start()
        self.addCleanup(api.stop)
        api.enqueue(
            CannedResponse(
                202, b'{"id":"1","status":"queued"}' + b" " * 2,
                {"Content-Type": "application/json"}, trickle=0.1,
            )
        )
        client = Naijamail(TEST_KEY, base_url=api.base_url, max_retries=0, timeout=0.5)
        started = time.monotonic()
        with self.assertRaises(NaijamailTimeoutError):
            client.emails.send(**MINIMAL)
        self.assertLess(time.monotonic() - started, 1.5)

    def test_each_retry_gets_a_fresh_deadline(self) -> None:
        api = MockAPI().start()
        self.addCleanup(api.stop)
        api.enqueue_accepted(delay=0.6)
        api.enqueue_accepted(delay=0.1)
        client = Naijamail(TEST_KEY, base_url=api.base_url, max_retries=1, timeout=0.4)
        self.assertEqual(client.emails.send(**MINIMAL).id, "5b1e0000-0000-4000-8000-000000000001")
        self.assertEqual(len(api.requests), 2)


if __name__ == "__main__":
    unittest.main()


class PayloadTooLargeTest(unittest.TestCase):
    def test_a_413_is_a_validation_error(self) -> None:
        # The server's body parser answers 413 to an oversized request. That is
        # the caller's input, never worth a retry, and should be caught by the
        # same except clause as every other refused payload.
        api = MockAPI().start()
        self.addCleanup(api.stop)
        client = Naijamail(TEST_KEY, base_url=api.base_url, max_retries=0)
        api.enqueue_error(413, "request entity too large")
        with self.assertRaises(ValidationError) as caught:
            client.emails.send(**MINIMAL)
        self.assertEqual(caught.exception.status_code, 413)
