"""GET /v1/emails/{id}."""

from __future__ import annotations

import unittest

from _support import TEST_KEY, MockAPI

from nc_email import Email, Naijamail, NotFoundError, ValidationError


class GetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.api = MockAPI().start()
        self.addCleanup(self.api.stop)
        self.client = Naijamail(TEST_KEY, base_url=self.api.base_url, max_retries=0)

    def test_retrieve(self) -> None:
        self.api.enqueue_json(
            200,
            {
                "id": "5b1e",
                "to": "x@y.com",
                "from": "hello@acme.com",
                "subject": "Hi",
                "status": "delivered",
                "created_at": "2026-08-29T10:00:00.000Z",
                "delivered_at": "2026-08-29T10:00:04.000Z",
                "opened": False,
                "clicked": True,
            },
        )
        email = self.client.emails.get("5b1e")

        self.assertIsInstance(email, Email)
        self.assertEqual(email.id, "5b1e")
        self.assertEqual(email.from_, "hello@acme.com")
        self.assertEqual(email.to, "x@y.com")
        self.assertEqual(email.status, "delivered")
        self.assertEqual(email.delivered_at, "2026-08-29T10:00:04.000Z")
        self.assertFalse(email.opened)
        self.assertTrue(email.clicked)
        self.assertIsNone(email.failure_reason)

        request = self.api.requests[-1]
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.path, "/v1/emails/5b1e")
        self.assertEqual(request.body, b"")
        # A GET is already idempotent; sending a dedup key on one would be noise.
        self.assertNotIn("idempotency-key", request.headers)

    def test_null_delivered_at(self) -> None:
        self.api.enqueue_json(
            200,
            {
                "id": "1",
                "to": "x@y.com",
                "from": "a@acme.com",
                "subject": "",
                "status": "queued",
                "created_at": "2026-08-29T10:00:00.000Z",
                "delivered_at": None,
                "opened": False,
                "clicked": False,
            },
        )
        self.assertIsNone(self.client.emails.get("1").delivered_at)

    def test_failure_reason(self) -> None:
        self.api.enqueue_json(
            200,
            {
                "id": "1",
                "to": "x@y.com",
                "from": "a@acme.com",
                "subject": "",
                "status": "bounced",
                "created_at": "2026-08-29T10:00:00.000Z",
                "opened": False,
                "clicked": False,
                "failure_reason": "550 5.1.1 unknown mailbox",
            },
        )
        self.assertEqual(self.client.emails.get("1").failure_reason, "550 5.1.1 unknown mailbox")

    def test_unknown_fields_are_ignored(self) -> None:
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
                "region": "af-west",
                "provider": "ses",
            },
        )
        self.assertEqual(self.client.emails.get("1").status, "sent")

    def test_unknown_id_returns_not_found_despite_the_400(self) -> None:
        # The control plane raises BadRequestException('message not found') for
        # an id it cannot find. Callers must still catch NotFoundError.
        self.api.enqueue_error(400, "message not found", error="Bad Request")
        with self.assertRaises(NotFoundError) as caught:
            self.client.emails.get("nope")
        self.assertEqual(caught.exception.status_code, 400)

    def test_other_400s_stay_validation_errors(self) -> None:
        self.api.enqueue_error(400, "id must be a UUID", error="Bad Request")
        with self.assertRaises(ValidationError):
            self.client.emails.get("nope")

    def test_id_is_percent_encoded(self) -> None:
        # An id from a caller's database must not be able to rewrite the path.
        self.api.enqueue_error(400, "message not found")
        with self.assertRaises(NotFoundError):
            self.client.emails.get("../../v1/admin?x=1")
        self.assertEqual(self.api.requests[-1].path, "/v1/emails/..%2F..%2Fv1%2Fadmin%3Fx%3D1")


if __name__ == "__main__":
    unittest.main()
