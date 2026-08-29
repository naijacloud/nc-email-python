"""Response objects: frozen, and tolerant of a server that ships ahead of us."""

from __future__ import annotations

import dataclasses
import unittest

from _support import TEST_KEY  # noqa: F401 - puts src/ on sys.path

from nc_email import (
    MESSAGE_STATUSES,
    Email,
    MessageStatus,
    RejectedRecipient,
    SendEmailResponse,
    WebhookEvent,
)


class FromDictTest(unittest.TestCase):
    def test_unknown_keys_are_ignored(self) -> None:
        for cls, data in [
            (SendEmailResponse, {"id": "1", "status": "queued", "tomorrows_field": True}),
            (
                Email,
                {
                    "id": "1",
                    "to": "x@y.com",
                    "from": "a@acme.com",
                    "subject": "",
                    "status": "sent",
                    "created_at": "now",
                    "opened": False,
                    "clicked": False,
                    "tomorrows_field": True,
                },
            ),
            (RejectedRecipient, {"address": "x@y.com", "reason": "suppressed", "why": "?"}),
            (WebhookEvent, {"type": "email.sent", "tomorrows_field": True}),
        ]:
            with self.subTest(cls=cls.__name__):
                self.assertIsInstance(cls.from_dict(data), cls)

    def test_missing_optional_keys_get_defaults(self) -> None:
        response = SendEmailResponse.from_dict({"id": "1", "status": "queued"})
        self.assertEqual(response.rejected, [])
        email = Email.from_dict({"id": "1"})
        self.assertEqual(email.subject, "")
        self.assertIsNone(email.delivered_at)
        self.assertFalse(email.opened)

    def test_a_malformed_rejected_entry_does_not_crash(self) -> None:
        response = SendEmailResponse.from_dict(
            {"id": "1", "status": "queued", "rejected": ["x@y.com", {"address": "a@b.com"}]}
        )
        self.assertEqual(response.rejected, [RejectedRecipient(address="a@b.com", reason="")])

    def test_rejected_of_the_wrong_type_becomes_empty(self) -> None:
        self.assertEqual(
            SendEmailResponse.from_dict({"id": "1", "status": "q", "rejected": "nope"}).rejected,
            [],
        )


class FrozenTest(unittest.TestCase):
    def test_responses_cannot_be_mutated(self) -> None:
        response = SendEmailResponse.from_dict({"id": "1", "status": "queued"})
        with self.assertRaises(dataclasses.FrozenInstanceError):
            response.id = "2"  # type: ignore[misc]


class MessageStatusTest(unittest.TestCase):
    def test_known_values(self) -> None:
        self.assertEqual(MessageStatus.QUEUED, "queued")
        self.assertEqual(MessageStatus.DELIVERED, "delivered")
        self.assertEqual(len(MESSAGE_STATUSES), 8)
        self.assertTrue(MessageStatus.is_known("bounced"))

    def test_an_unknown_status_passes_through_rather_than_raising(self) -> None:
        # A strict enum would make the day the platform adds a status the day
        # every deployed SDK older than it starts throwing.
        self.assertFalse(MessageStatus.is_known("shredded"))
        self.assertEqual(
            SendEmailResponse.from_dict({"id": "1", "status": "shredded"}).status, "shredded"
        )


if __name__ == "__main__":
    unittest.main()
